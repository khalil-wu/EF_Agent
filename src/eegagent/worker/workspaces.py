"""Mounted local directories and reversible file operations within their roots."""
import json
from pathlib import Path
import re
import uuid

from eegagent.worker.store import storage_dir


def catalog():
    directory = storage_dir() / "workspaces"
    if not directory.is_dir():
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def load(workspace_id):
    if not re.fullmatch(r"ws_[0-9a-f]{12}", workspace_id):
        raise ValueError("无效工作区")
    return json.loads((catalog() / (workspace_id + ".json")).read_text(encoding="utf-8"))


def save(record):
    path = catalog() / (record["workspace_id"] + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def list_workspaces(include_removed=False):
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(catalog().glob("ws_*.json"))]
    return records if include_removed else [record for record in records if not record.get("removed", False)]


def mount(path, name=""):
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("请选择一个本地文件夹")
    for record in list_workspaces(include_removed=True):
        if Path(record["root"]) == root:
            if record.pop("removed", False):
                save(record)
            return record
    record = {"workspace_id": "ws_" + uuid.uuid4().hex[:12], "name": name or root.name,
              "root": str(root), "excluded": [], "included": [], "trash": []}
    save(record)
    return record


def unmount(workspace_id):
    record = load(workspace_id)
    record["removed"] = True
    save(record)
    return record


def resolve(record, path):
    root = Path(record["root"]).resolve(strict=True)
    candidate = (root / path).resolve()
    candidate.relative_to(root)
    return candidate


def relative(record, path):
    return path.relative_to(Path(record["root"])).as_posix()


def excluded(record, path):
    value = relative(record, path)
    if any(part.startswith(".") for part in Path(value).parts):
        return True
    if any(value == item or value.startswith(item + "/") for item in record.get("included", [])):
        return False
    return any(value == item or value.startswith(item + "/") for item in record["excluded"])


def listing(workspace_id, path=""):
    record = load(workspace_id)
    directory = resolve(record, path)
    entries = []
    for item in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if item.name.startswith("."):
            continue
        resolved = resolve(record, relative(record, item))
        entries.append({"path": relative(record, item), "name": item.name, "directory": resolved.is_dir(),
                        "excluded": excluded(record, item), "size": 0 if resolved.is_dir() else resolved.stat().st_size})
    return {"workspace": record, "path": relative(record, directory), "entries": entries}


def reference_items(workspace_id, paths):
    record = load(workspace_id)
    items = []
    for path in paths:
        source = resolve(record, path)
        if not source.exists():
            raise FileNotFoundError(f"文件已移动或不存在：{path}")
        items.append({"workspace_id": workspace_id, "path": relative(record, source),
                      "name": source.name, "kind": "directory" if source.is_dir() else "file",
                      "excluded": excluded(record, source)})
    return items


def mentions(workspace_id, query="", limit=40):
    import os
    record = load(workspace_id)
    query = query.replace("\\", "/")
    root = Path(record["root"])
    paths = []
    if query in ["", ".", "./"]:
        paths = [root, *sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))]
    elif "/" in query:
        parent, _, name = query.rpartition("/")
        directory = resolve(record, parent)
        paths = [p for p in directory.iterdir() if name.lower() in p.name.lower()]
    else:
        ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".eegagent-trash"}
        for parent, directories, files in os.walk(root):
            candidates = [Path(parent)/name for name in sorted(directories)+sorted(files)]
            paths.extend(p for p in candidates if query.casefold() in relative(record, p).casefold())
            directories[:] = [name for name in directories if name not in ignored]
            if len(paths) >= limit:
                break
    paths = [p for p in paths if p.name != ".eegagent-trash"]
    paths.sort(key=lambda p: (p != root, not p.is_dir(), p.name.casefold() != query.casefold(), p.name.casefold()))
    return {"items": reference_items(workspace_id, [relative(record, p) for p in paths[:limit]])}


def relocate(record, source, destination):
    if source == Path(record["root"]):
        raise ValueError("不能移动工作区根目录")
    if destination.exists():
        raise FileExistsError("目标名称已存在")
    old = relative(record, source)
    source.rename(destination)
    new = relative(record, destination)
    for field in ["excluded", "included"]:
        record[field] = [new + item[len(old):] if item == old or item.startswith(old + "/") else item for item in record.get(field, [])]
    # Existing scientific records keep their file identities when a directory is moved.
    for path in (storage_dir() / "manifests").glob("*.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        def adjust(value):
            nonlocal changed
            current = Path(value)
            if current == source or source in current.parents:
                changed = True
                return str(destination / current.relative_to(source))
            return value
        if manifest.get("source_path"):
            manifest["source_path"] = adjust(manifest["source_path"])
        if manifest.get("source_files"):
            manifest["source_files"] = [adjust(value) for value in manifest["source_files"]]
        if changed:
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def record_group(record, source):
    if source.is_file() and source.suffix.lower() in [".vhdr", ".vmrk", ".eeg", ".set", ".fdt"]:
        from eegagent.worker.readers import resolve_main_file, record_files
        return [resolve(record, relative(record, member)) for member in record_files(resolve_main_file(source))]
    return [source]


def operations(workspace_id, action, paths=None, destination="", trash_id=""):
    record = load(workspace_id)
    paths = paths or []
    sources = [resolve(record, path) for path in paths]
    moves = []
    if action in ["exclude", "include"]:
        members = list(dict.fromkeys(member for source in sources for member in record_group(record,source)))
        for source in members:
            if not source.exists():
                raise FileNotFoundError(str(source))
            value = relative(record, source)
            if action == "exclude" and value not in record["excluded"]:
                record["excluded"].append(value)
                record["included"] = [item for item in record.get("included", []) if item != value and not item.startswith(value + "/")]
            if action == "include":
                record["excluded"] = [item for item in record["excluded"] if item != value and not item.startswith(value + "/")]
                if value not in record.get("included", []):
                    record.setdefault("included", []).append(value)
    elif action == "mkdir":
        resolve(record, destination).mkdir()
    elif action in ["rename", "move", "trash", "restore"]:
        sources = [source for source in dict.fromkeys(sources) if not any(parent in sources for parent in source.parents)]
        plan = []
        seen = set()
        recovery = None
        if action == "restore":
            recovery = next(item for item in record["trash"] if item["id"] == trash_id)
            plan = [(resolve(record, item["stored"]), resolve(record, item["original"])) for item in recovery["members"]]
        else:
            if action == "rename" and len(sources) != 1:
                raise ValueError("重命名时选择一个文件或文件夹")
            target = resolve(record, destination)
            for source in sources:
                if source in seen:
                    continue
                if source == Path(record["root"]):
                    raise ValueError("不能移动工作区根目录")
                members = record_group(record,source)
                if action == "rename":
                    if source != members[0]:
                        raise ValueError("附属文件重命名请选择它的记录头文件或所在文件夹")
                    members = [source]
                if action == "trash":
                    identifier = uuid.uuid4().hex[:12]
                    target = resolve(record, ".eegagent-trash/" + identifier)
                    target.mkdir(parents=True)
                    recovery = {"id": identifier, "original": relative(record, source), "members": []}
                    record["trash"].append(recovery)
                for member in members:
                    member = resolve(record, relative(record, member))
                    if member in seen:
                        continue
                    seen.add(member)
                    dest = target if action == "rename" else target / member.name
                    dest = resolve(record, relative(record, dest))
                    plan.append((member, dest))
                    if action == "trash":
                        recovery["members"].append({"original": relative(record, member), "stored": relative(record, dest)})
        for source, dest in plan:
            if not source.exists():
                raise FileNotFoundError(str(source))
            if dest.exists():
                raise FileExistsError("目标名称已存在：" + dest.name)
        if len({dest for source, dest in plan}) != len(plan):
            raise ValueError("选中的记录在目标目录有重名文件")
        for source, dest in plan:
            relocate(record, source, dest)
            moves.append({"source":str(source),"destination":str(dest)})
        if action == "restore":
            record["trash"].remove(recovery)
    else:
        raise ValueError("未知文件操作")
    save(record)
    return {"status": "ok", "workspace": record, "action": action, "paths": paths, "relocations":moves}


def workspace_files(args, workspace_id, session_id=None):
    identifier = workspace_id or args.get("workspace_id")
    if not identifier:
        raise ValueError("先挂接并选择一个工作区")
    if args["action"] == "inspect":
        from eegagent.worker.files import EEG_EXTS
        from eegagent.worker.attachments import VIDEO_EXTS, DOCUMENT_EXTS, IMAGE_EXTS, register_upload
        from eegagent.worker.readers import file_ext
        from eegagent.worker.text import TEXT_TABLE_EXTS
        source = resolve(load(identifier), args["path"])
        if file_ext(source) not in VIDEO_EXTS | DOCUMENT_EXTS | IMAGE_EXTS | EEG_EXTS | TEXT_TABLE_EXTS:
            raise ValueError("此文件用 read_file 按文本读取")
        return register_upload(str(source), session_id)
    if args["action"] == "list":
        return {"status": "ok", **listing(identifier, args.get("path", ""))}
    return operations(identifier, args["action"], args.get("paths"), args.get("destination", ""), args.get("trash_id", ""))


def quality(workspace_id, session_id, pattern=None, path="", sample_rate_hz=None):
    """Measure every included EEG file and trial without model round trips per file."""
    import csv
    from eegagent.worker import script_jobs
    from eegagent.worker.files import list_files
    from eegagent.worker.inspect_record import inspect_record
    from eegagent.worker.measurements import quality_summary
    from eegagent.worker.progress import report
    from eegagent.worker.processing import _public
    from eegagent.worker.store import make_artifact_id, artifact_payload_path, save_artifact, save_manifest
    record = load(workspace_id)
    files = []
    offset = 0
    while True:
        page = list_files(pattern, "eeg", str(resolve(record, path)), 200, True, session_id, workspace_id, offset,
                          sample_rate_hz=sample_rate_hz)
        files.extend(page["files"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    job = script_jobs.ScriptJob()
    with script_jobs.lock:
        if session_id in script_jobs.active:
            raise ValueError("当前会话已有执行中的任务")
        script_jobs.active[session_id] = job
    rows, manifests = [], []
    try:
        for index, file in enumerate(files):
            report("local_quality", "本地质量检查", f"{index+1}/{len(files)} · {file['name']}")
            try:
                first = inspect_record(file["path"], sample_rate_hz=sample_rate_hz)
                for trial in range(first.get("trial_count") or 1):
                    report("local_quality","本地质量检查",f"{file['name']} · 试次 {trial+1}/{first.get('trial_count') or 1} · 文件 {index+1}/{len(files)}")
                    if job.cancelled:
                        raise InterruptedError("本地质量检查已停止")
                    manifest = first if trial == 0 else inspect_record(file["path"], sample_rate_hz=sample_rate_hz, trial_index=trial)
                    measured = quality_summary(manifest["manifest_id"])
                    manifest["quality"] = measured
                    save_manifest(manifest)
                    manifests.append(manifest["manifest_id"])
                    rows.append({"file": relative(record, Path(file["path"])), "trial": trial,
                                 "status": "measured", "sample_rate_hz": manifest["sample_rate_hz"],
                                 "start_s": measured["sample_time_range_s"][0], "end_s": measured["sample_time_range_s"][1],
                                 "channels": len(measured["channels"]), "flat_channels": ",".join(measured["flat_channels"]),
                                 "line_noise_ratio": measured["line_noise_ratio"], "error": ""})
            except (ValueError, FileNotFoundError, PermissionError) as error:
                rows.append({"file": relative(record, Path(file["path"])), "trial": None, "status": "not_measured", "error": str(error)})
        identifier = make_artifact_id("workspace_quality", "1.0", {"workspace_id": workspace_id, "run": uuid.uuid4().hex})
        target = artifact_payload_path(identifier, "csv")
        columns = ["file", "trial", "status", "sample_rate_hz", "start_s", "end_s", "channels", "flat_channels", "line_noise_ratio", "error"]
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns);writer.writeheader();writer.writerows(rows)
        summary = {"title": "工作区信号质量.csv", "format": "csv", "file_count": len(files), "rows": len(rows),
                   "measured": sum(row["status"]=="measured" for row in rows),
                   "not_measured": sum(row["status"]!="measured" for row in rows), "preview": rows[:12],
                   "files_with_flat_channels": sorted({row["file"] for row in rows if row.get("flat_channels")}),
                   "workspace_id": workspace_id, "excluded": record["excluded"]}
        artifact = save_artifact({"artifact_id": identifier, "kind": "table", "media_type": "text/csv", "uri": str(target),
                                  "source_manifest_ids": manifests, "parent_artifact_ids": [], "summary": summary,
                                  "provenance": {"created_by": "workspace_quality@1.0", "params": {"pattern": pattern, "sample_rate_hz": sample_rate_hz}, "steps": ["full_file_full_trial_quality"]}})
        return {"status": "ok", "artifact": _public(artifact), "summary": summary}
    finally:
        with script_jobs.lock:
            script_jobs.active.pop(session_id)
        job.done.set()
