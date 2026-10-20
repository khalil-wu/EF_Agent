"""Filesystem boundary of the worker: allowed roots, list_files, read_file.

Readable roots = storage/ (uploads, manifests, artifacts, scratch) + EEGAGENT_DATA_DIRS.
Writable roots = storage/scratch/<session>/ and storage/artifacts/ (only through the sandbox API).
Nothing outside these roots is ever listed, read or written. This module and sandbox_runner.py are the
whole security boundary of the worker; run the worker in a container for stronger isolation.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from pathlib import Path

from eegagent.worker.store import ensure_layout, load_artifact, resolve_inside_storage, storage_dir
from eegagent.worker.readers import MNE_EXTS, NATIVE_EXTS, file_ext, resolve_main_file

EEG_EXTS = MNE_EXTS | NATIVE_EXTS
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}
MAX_LIST = 200
READ_MAX_LINES = 2000
READ_MAX_BYTES = 50 * 1024
SESSION_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def data_dirs(include_workspaces=True) -> list[Path]:
    out: list[Path] = []
    for raw in os.environ.get("EEGAGENT_DATA_DIRS", "").split(";"):
        raw = raw.strip()
        if raw:
            p = Path(raw).expanduser()
            if p.exists() and p.is_dir():
                out.append(p.resolve())
    from eegagent.worker.workspaces import list_workspaces
    if include_workspaces:
        out.extend(Path(record["root"]) for record in list_workspaces())
    return out


def data_roots() -> list[Path]:
    """Roots that hold user data (uploads + configured data dirs)."""
    return [ensure_layout() / "uploads"] + data_dirs()


def scratch_dir(session_id: str | None) -> Path:
    sid = session_id if session_id and SESSION_RE.match(session_id) else "anon"
    p = ensure_layout() / "scratch" / sid
    p.mkdir(parents=True, exist_ok=True)
    return p


def readable_roots(session_id: str | None = None) -> list[Path]:
    """Everything the model may read: whole storage dir (uploads/manifests/artifacts/scratch) + data dirs."""
    roots = [storage_dir().resolve()] + data_dirs()
    if session_id:
        roots.append(scratch_dir(session_id).resolve())
    return roots


def _resolve_under(path: str | Path, roots: list[Path]) -> Path:
    candidate = Path(path)
    missing = []
    for root in roots:
        c = candidate if candidate.is_absolute() else root / candidate
        try:
            c = c.resolve()
            c.relative_to(root)
        except (ValueError, OSError):
            continue
        if c.exists():
            return c
        missing.append(c)
    if missing:
        return missing[0]
    raise PermissionError(f"path outside allowed roots: {candidate}")


def resolve_readable(path: str | Path, session_id: str | None = None) -> Path:
    return _resolve_under(path, readable_roots(session_id))


def resolve_inside_roots(path: str | Path) -> Path:
    """Data-file resolution used by inspect_record / signals (uploads, data dirs, storage)."""
    return _resolve_under(path, readable_roots(None))


# ---------------------------------------------------------------- list_files

def list_files(pattern: str | None = None, kind: str = "eeg", root: str | None = None, limit: int = 50,
               recursive: bool = True, session_id: str | None = None, workspace_id: str | None = None, offset: int = 0,
               sample_rate_hz: float | None = None) -> dict:
    from eegagent.worker.workspaces import load as load_workspace, resolve as resolve_workspace, excluded
    from eegagent.worker.text import TEXT_TABLE_EXTS, classify_text_table
    workspace = load_workspace(workspace_id) if workspace_id else None
    if workspace:
        root = str(resolve_workspace(workspace, root or ""))
    roots = data_roots()
    if session_id:
        roots = [ensure_layout() / "uploads" / session_id, *data_dirs(False), scratch_dir(session_id)]
    allowed = readable_roots(session_id)
    if root:
        try:
            want = _resolve_under(root, allowed)
        except PermissionError:
            return {"status": "error", "error": {"code": "forbidden_root", "message": f"{root} 不在允许的数据目录内", "recoverable": True},
                    "allowed_roots": [str(r) for r in allowed]}
        if not want.is_dir():
            return {"status": "error", "error": {"code": "not_a_dir", "message": f"{root} 不是目录", "recoverable": True}}
        roots = [want]
    if kind == "eeg":
        exts = EEG_EXTS
    elif kind == "video":
        exts = VIDEO_EXTS
    elif kind == "any":
        exts = EEG_EXTS | VIDEO_EXTS
    else:
        exts = None  # all
    limit = max(1, min(int(limit), MAX_LIST))
    rows: list[dict] = []
    for r in roots:
        walker = r.rglob("*") if recursive else r.glob("*")
        for p in walker:
            if not p.is_file() and not (p.is_dir() and file_ext(p) == ".mff"):
                continue
            if (workspace and excluded(workspace, p)) or any(part.startswith(".") for part in p.relative_to(r).parts):
                continue
            extension = file_ext(p)
            if exts is not None and extension not in exts:
                continue
            if kind == "eeg" and extension in TEXT_TABLE_EXTS and classify_text_table(p, sample_rate_hz=sample_rate_hz) != "signal":
                continue
            if extension == ".eeg" and resolve_main_file(p) != p:
                continue
            if pattern and not fnmatch.fnmatch(p.name.lower(), pattern.lower()):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            size = sum(member.stat().st_size for member in p.rglob("*") if member.is_file()) if p.is_dir() else st.st_size
            rows.append({"path": str(p), "name": p.name, "ext": extension, "size_mb": round(size / 1e6, 3), "root": str(r)})
    rows.sort(key=lambda x: (x["root"], x["name"]))
    total = len(rows)
    page = rows[offset:offset+limit]
    return {"status": "ok", "roots": [str(r) for r in roots], "files": page, "total": total, "offset": offset, "next_offset": offset+len(page) if offset+len(page)<total else None, "truncated": offset+len(page)<total,
            "note": "EEG 文件把 path 交给 inspect_record 读取（npy/csv 需用户提供采样率）；文本文件用 read_file" if rows
            else "没有匹配的文件；请让用户上传，或在 EEGAGENT_DATA_DIRS 里加入数据目录"}


# ---------------------------------------------------------------- read_file

def read_file(path: str, offset: int = 1, limit: int = 200, session_id: str | None = None,
              workspace_id: str | None = None) -> dict:
    if workspace_id and not Path(path).is_absolute():
        from eegagent.worker.workspaces import load, resolve
        p = resolve(load(workspace_id), path)
    else:
        p = resolve_readable(path, session_id)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(str(p))
    with p.open("rb") as stream:
        head = stream.read(4096)
    if b"\x00" in head:
        return {"status": "error", "error": {"code": "binary_file", "recoverable": True,
                                             "message": "二进制文件不能用 read_file；EEG 用 inspect_record，产物用 read_artifact，其它在 run_python 里解析"}}
    from eegagent.worker.text import read_text
    text, encoding = read_text(p)
    lines = text.splitlines()
    total = len(lines)
    offset = max(1, int(offset))
    limit = max(1, min(int(limit), READ_MAX_LINES))
    sel = lines[offset - 1: offset - 1 + limit]
    out_lines: list[str] = []
    nbytes = 0
    truncated_by_bytes = False
    for ln in sel:
        b = len(ln.encode("utf-8")) + 1
        if nbytes + b > READ_MAX_BYTES:
            truncated_by_bytes = True
            break
        out_lines.append(ln)
        nbytes += b
    end = offset - 1 + len(out_lines)
    return {"status": "ok", "path": str(p), "encoding": encoding, "total_lines": total, "start_line": offset, "end_line": end,
            "content": "\n".join(out_lines), "truncated": end < total or truncated_by_bytes,
            "note": f"显示第 {offset}–{end} 行，共 {total} 行；继续读用 offset={end + 1}" if end < total else ""}


def _workspace_target(workspace_id: str, path: str):
    from eegagent.worker.workspaces import load, resolve
    if not workspace_id:
        raise ValueError("先挂接并选择一个工作区")
    if Path(path).is_absolute():
        raise ValueError("path 使用当前工作区内的相对文件路径")
    workspace = load(workspace_id)
    return workspace, resolve(workspace, path)


def _written_file(workspace: dict, target: Path, action: str) -> dict:
    relative_path = target.relative_to(Path(workspace["root"])).as_posix()
    return {"status": "ok", "workspace_id": workspace["workspace_id"], "path": str(target),
            "relative_path": relative_path, "name": target.name, "size": target.stat().st_size, "action": action,
            "workspace_file": {"workspace_id": workspace["workspace_id"], "path": relative_path,
                               "name": target.name, "kind": "file"}}


def write_file(workspace_id: str, path: str, content: str, overwrite: bool = False) -> dict:
    """Write a protocol, recipe, script or report in the selected workspace."""
    workspace, target = _workspace_target(workspace_id, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    with target.open("w" if overwrite else "x", encoding="utf-8", newline="") as stream:
        stream.write(content)
    return _written_file(workspace, target, "overwritten" if existed else "created")


def edit_file(workspace_id: str, path: str, old_text: str, new_text: str, replace_all: bool = False) -> dict:
    """Apply an exact text replacement, reporting ambiguous matches instead of guessing."""
    workspace, target = _workspace_target(workspace_id, path)
    if not old_text:
        raise ValueError("old_text 不能为空；创建或完整替换文件使用 write_file")
    from eegagent.worker.text import decode_text
    original = target.read_bytes()
    _, encoding = decode_text(original)
    content = original.decode(encoding)
    matches = content.count(old_text)
    if matches == 0:
        raise ValueError("文件中没有找到 old_text；先 read_file 查看实际内容")
    if matches > 1 and not replace_all:
        raise ValueError(f"old_text 匹配 {matches} 处；补充唯一上下文，或设置 replace_all=true")
    updated = content.replace(old_text, new_text, -1 if replace_all else 1)
    target.write_bytes(updated.encode(encoding))
    return {**_written_file(workspace, target, "edited"), "replacements": matches if replace_all else 1}


def save_to_workspace(workspace_id: str, artifact_id: str, path: str, overwrite: bool = False) -> dict:
    """Copy a registered artifact payload to the user's chosen workspace filename."""
    workspace, target = _workspace_target(workspace_id, path)
    artifact = load_artifact(artifact_id)
    source = resolve_inside_storage(artifact["uri"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        shutil.copyfile(source, target)
    else:
        with source.open("rb") as src, target.open("xb") as dst:
            shutil.copyfileobj(src, dst)
    written = _written_file(workspace, target, "saved")
    written["workspace_file"]["artifact_id"] = artifact_id
    return {**written, "artifact_id": artifact_id}
