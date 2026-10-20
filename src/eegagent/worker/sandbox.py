"""run_python: execute model-written Python in a sandboxed subprocess with the `eeg` helper API.

Mirrors pi's bash tool contract: timeout, tail truncation (2000 lines / 50 KB), full output kept on disk,
non-zero → error result the model can read. The sandbox itself lives in sandbox_runner.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from eegagent.worker.files import data_dirs, scratch_dir
from eegagent.worker.store import artifact_payload_path, ensure_layout, make_artifact_id, save_artifact, storage_dir

VERSION = "1.3"
MAX_LINES = 2000
MAX_BYTES = 50 * 1024
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 900
INTERNAL_PREFIXES = (".run_", ".mpl", ".tmp", ".home")
INTERNAL_SUFFIXES = (".out", ".result.json", ".cfg.json", ".pyc")
DELIVERY_FORMATS = {
    ".csv": ("table", "text/csv"), ".tsv": ("table", "text/tab-separated-values"),
    ".json": ("report", "application/json"), ".md": ("report", "text/markdown"),
    ".txt": ("report", "text/plain"), ".html": ("report", "text/html"),
    ".pdf": ("report", "application/pdf"), ".py": ("report", "text/x-python"),
    ".xlsx": ("report", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".docx": ("report", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".png": ("plot", "image/png"), ".jpg": ("plot", "image/jpeg"),
    ".jpeg": ("plot", "image/jpeg"), ".svg": ("plot", "image/svg+xml"), ".webp": ("plot", "image/webp"),
}


def tail_truncate(text: str, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES) -> tuple[str, bool]:
    lines = text.splitlines()
    out: list[str] = []
    nbytes = 0
    for ln in reversed(lines):
        b = len(ln.encode("utf-8")) + 1
        if len(out) >= max_lines or nbytes + b > max_bytes:
            break
        out.append(ln)
        nbytes += b
    out.reverse()
    truncated = len(out) < len(lines)
    return "\n".join(out), truncated


def _snapshot(scratch: Path) -> dict[str, float]:
    snap: dict[str, float] = {}
    for p in scratch.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(scratch)).replace("\\", "/")
        if rel.startswith(INTERNAL_PREFIXES) or rel.endswith(INTERNAL_SUFFIXES) or "/.mpl/" in "/" + rel or "/.tmp/" in "/" + rel:
            continue
        try:
            snap[rel] = p.stat().st_mtime
        except OSError:
            pass
    return snap


def _next_index(scratch: Path) -> int:
    n = 0
    for p in scratch.glob("run_*.py"):
        try:
            n = max(n, int(p.stem.split("_")[1]))
        except (IndexError, ValueError):
            pass
    return n + 1


def _deliver_files(result: dict, files: list[str], scratch: Path, session_id: str, run_id: str, code_sha: str) -> list[dict]:
    """Register real script outputs so ordinary file writes have the same delivery path as eeg.save_* outputs."""
    artifacts = list(result.get("artifacts", []))
    known = {Path(record["uri"]).resolve() for record in artifacts}
    manifests = sorted(set(result.get("source_manifest_ids", [])) |
                       {record["manifest_id"] for record in result.get("manifests", [])} |
                       {identifier for record in artifacts for identifier in record.get("source_manifest_ids", [])})
    parents = sorted(set(result.get("parent_artifact_ids", [])) | {record["artifact_id"] for record in artifacts})
    from eegagent.worker.processing import _public
    for relative in files:
        source = scratch / relative
        if (source.suffix.lower() not in DELIVERY_FORMATS or any(part.startswith(".") for part in Path(relative).parts)
                or re.fullmatch(r"run_\d+\.(py|out|err)", source.name) or source.resolve() in known):
            continue
        kind, media = DELIVERY_FORMATS[source.suffix.lower()]
        identifier = make_artifact_id("run_python_file", VERSION, {"session_id": session_id, "run_id": run_id,
                                                                     "code_sha": code_sha, "path": relative})
        target = artifact_payload_path(identifier, source.suffix)
        shutil.copyfile(source, target)
        record = save_artifact({"artifact_id": identifier, "kind": kind, "uri": str(target), "media_type": media,
                                "source_manifest_ids": manifests, "parent_artifact_ids": parents,
                                "summary": {"title": source.name, "format": source.suffix[1:].lower(), "source_path": relative},
                                "provenance": {"created_by": f"run_python@{VERSION}",
                                               "params": {"session_id": session_id, "run_id": run_id, "code_sha": code_sha,
                                                          "path": relative}, "steps": ["script", "save_script_file"]}})
        artifacts.append(_public(record))
    return artifacts


def _preexec():  # POSIX only: cap address space
    try:
        import resource

        mb = int(os.environ.get("EEGAGENT_SANDBOX_MEM_MB", "2048"))
        resource.setrlimit(resource.RLIMIT_AS, (mb << 20, mb << 20))
    except Exception:  # noqa: BLE001
        pass


def run_python(code: str, session_id: str | None, timeout_s: float | None = None, label: str | None = None,
               workspace_id: str | None = None) -> dict:
    from eegagent.worker import script_jobs
    sid = session_id or "anon"
    job = script_jobs.ScriptJob()
    with script_jobs.lock:
        if sid in script_jobs.active:
            raise ValueError("本会话已有脚本正在执行，请先停止或等待完成")
        script_jobs.active[sid] = job
    try:
        return _run_python(code, sid, timeout_s, label, job, workspace_id)
    finally:
        with script_jobs.lock:
            script_jobs.active.pop(sid)
        job.done.set()


def _run_python(code: str, session_id: str, timeout_s: float | None, label: str | None, job,
                workspace_id: str | None = None) -> dict:
    if not code or not code.strip():
        raise ValueError("code 为空")
    timeout_s = float(timeout_s or DEFAULT_TIMEOUT_S)
    timeout_s = max(1.0, min(timeout_s, MAX_TIMEOUT_S))
    sid = session_id or "anon"
    scratch = scratch_dir(sid)
    ensure_layout()
    from eegagent.knowledge.documents import document_dir
    from eegagent.worker.registry import registry_path
    documents = document_dir(sid)
    documents.mkdir(parents=True, exist_ok=True)
    model_registry = registry_path().resolve()
    workspace_root = None
    if workspace_id:
        from eegagent.worker.workspaces import load as load_workspace
        workspace_root = load_workspace(workspace_id)["root"]
    n = _next_index(scratch)
    run_id = f"run_{n:03d}"
    code_path = scratch / f"{run_id}.py"
    header = f"# {label}\n" if label else ""
    code_path.write_text(header + code, encoding="utf-8")
    code_sha = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
    cfg_path = scratch / f".{run_id}.cfg.json"
    result_path = scratch / f".{run_id}.result.json"
    out_path = scratch / f"{run_id}.out"
    progress_path = scratch / f".{run_id}.events.jsonl"
    progress_path.touch()
    src_dir = str(Path(__file__).resolve().parents[2])
    cfg = {
        "session_id": sid, "scratch": str(scratch), "run_id": run_id, "code_sha": code_sha,
        "code_path": str(code_path), "result_path": str(result_path), "src_dir": src_dir,
        "workspace_id": workspace_id, "workspace_root": workspace_root,
        "progress_path": str(progress_path),
        "read_roots": [str(storage_dir().resolve()), str(model_registry)] + ([workspace_root] if workspace_root else [str(d) for d in data_dirs()]),
        "write_roots": [str(scratch), str(ensure_layout() / "artifacts")],
        "tool_write_roots": [str(storage_dir() / "manifests"), str(documents)],
    }
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    before = _snapshot(scratch)

    env = {k: v for k, v in os.environ.items() if not k.startswith("EEGAGENT_LLM")}
    tmp_dir = scratch / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    home = scratch / ".home"
    home.mkdir(exist_ok=True)
    env.update({
        "PYTHONPATH": src_dir + os.pathsep + env.get("PYTHONPATH", ""),
        "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg",
        "EEGAGENT_STORAGE_DIR": str(storage_dir()), "EEGAGENT_SANDBOX": "1",
        "EEGAGENT_MODEL_REGISTRY": str(model_registry),
        "TMPDIR": str(tmp_dir), "TEMP": str(tmp_dir), "TMP": str(tmp_dir),
        # MNE keeps its config under a fake home inside scratch; numba caches there too; joblib stays in-process
        # (subprocesses are blocked), so n_jobs>1 degrades to sequential instead of failing. MNE's INFO log and
        # library deprecation notices would otherwise fill the 50 KB stdout budget the model reads.
        "_MNE_FAKE_HOME_DIR": str(home), "MNE_DATA": str(home / "mne_data"), "NUMBA_CACHE_DIR": str(tmp_dir / "numba"),
        "JOBLIB_MULTIPROCESSING": "0", "TORCH_HOME": str(home / "torch"), "MNE_LOGGING_LEVEL": "WARNING",
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
    })
    runner = str(Path(__file__).with_name("sandbox_runner.py"))
    cmd = [sys.executable, "-u", runner, str(cfg_path)]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["preexec_fn"] = _preexec
    t0 = time.monotonic()
    timed_out = False
    from eegagent.worker import script_jobs
    with script_jobs.lock:
        if job.cancelled:
            job.result = {"status": "error", "error": {"code": "Cancelled", "message": "用户已停止脚本", "recoverable": True}}
            return job.result
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                cwd=str(scratch), text=True, encoding="utf-8", errors="replace", **kwargs)
        job.process = proc
    from eegagent.worker.progress import forward
    pending = ""
    with progress_path.open(encoding="utf-8") as stages:
        while True:
            pending += stages.read()
            lines = pending.split("\n")
            pending = lines.pop()
            for line in lines:
                forward(json.loads(line))
            remaining = t0 + timeout_s - time.monotonic()
            if remaining <= 0:
                timed_out = True
                proc.kill()
                output, _ = proc.communicate()
                break
            try:
                output, _ = proc.communicate(timeout=min(.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        for line in (pending + stages.read()).splitlines():
            forward(json.loads(line))
    exit_code = proc.returncode
    elapsed = round(time.monotonic() - t0, 3)
    out_path.write_text(output, encoding="utf-8")
    tail, truncated = tail_truncate(output)

    result: dict = {"ok": False, "artifacts": [], "error": None}
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if job.cancelled:
        result["ok"] = False
        result["error"] = {"type": "Cancelled", "message": "用户已停止脚本"}
    elif timed_out:
        result["ok"] = False
        result["error"] = {"type": "Timeout", "message": f"脚本超过 {timeout_s:g} s 被终止；缩小数据范围或用 max_windows 限制预算"}
    elif not result_path.exists():
        result["error"] = {"type": "RunnerCrashed", "message": f"exit code {exit_code}", "traceback": tail[-1500:]}

    after = _snapshot(scratch)
    files = sorted(rel for rel, mt in after.items() if rel not in before or before[rel] != mt)
    files = [f for f in files if f != f"{run_id}.py"]

    status = "ok" if result.get("ok") else "error"
    out = {
        "status": status, "run_id": run_id, "code_path": str(code_path), "elapsed_s": elapsed,
        "stdout": tail, "stdout_truncated": truncated, "full_output_path": str(out_path),
        "artifacts": _deliver_files(result, files, scratch, sid, run_id, code_sha), "files": files, "scratch": str(scratch),
        "manifests": result.get("manifests", []), "workspace_files": result.get("workspace_files", []),
    }
    if status == "error":
        err = result.get("error") or {"type": "Error", "message": "unknown"}
        out["error"] = {"code": err.get("type", "Error"), "message": err.get("message", ""), "traceback": err.get("traceback", ""),
                        "recoverable": True}
    job.result = out
    return out
