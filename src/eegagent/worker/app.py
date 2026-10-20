"""FastAPI entry point for the signal worker.

    uvicorn eegagent.worker.app:app --host 0.0.0.0 --port 8100

Endpoints
  GET  /health
  GET  /tools                 tool definitions (contracts/tool.schema.json shape)
  POST /tools/{name}          {"args": {...}} -> {"status": "ok"|"error", ...}
  GET  /manifests/{id}
  GET  /artifacts/{id}        record
  GET  /artifacts/{id}/file   payload (png / csv / json / md / npy)
  GET  /knowledge             index stats
  GET  /knowledge/{chunk_id}  one chunk
"""

from __future__ import annotations

import traceback
import json
from queue import Queue
from typing import Literal
from threading import Thread

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from eegagent.worker.progress import channel
from pydantic import BaseModel

from eegagent.worker import knowledge
from eegagent.worker.store import ensure_layout, load_artifact, load_manifest, resolve_inside_storage
from eegagent.worker.tools import CLEANING_METHODS, CLEANING_MISSING, DISPATCH, TOOL_DEFINITIONS, run_tool
from eegagent.worker.script_jobs import stop_script
from eegagent.worker.models import list_models
from eegagent.knowledge.documents import get_document_chunk
from eegagent.worker import workspaces
from eegagent.worker.folder_picker import pick_directory

app = FastAPI(title="EFAgent signal worker", version="0.3")

class WorkspaceOperation(BaseModel):
    action: Literal["exclude","include","mkdir","rename","move","trash","restore"]
    paths: list[str] = []
    destination: str = ""
    trash_id: str = ""


class WorkspaceMount(BaseModel):
    path: str
    name: str = ""


@app.get("/workspaces")
def workspace_catalog():
    return workspaces.list_workspaces()


@app.post("/workspaces")
def mount_workspace(body: WorkspaceMount):
    try:
        return workspaces.mount(body.path, body.name)
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.post("/workspaces/pick")
def pick_workspace():
    try:
        path = pick_directory()
        if path is None:
            return {"cancelled": True}
        return workspaces.mount(path)
    except (OSError, ValueError, RuntimeError) as error:
        return JSONResponse({"error": str(error)}, status_code=500)


@app.get("/workspaces/{workspace_id}")
def workspace_listing(workspace_id: str, path: str = ""):
    try:
        return workspaces.listing(workspace_id, path)
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.delete("/workspaces/{workspace_id}")
def unmount_workspace(workspace_id: str):
    try:
        return workspaces.unmount(workspace_id)
    except FileNotFoundError:
        return JSONResponse({"error": "工作区不存在"}, status_code=404)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.get("/workspaces/{workspace_id}/file")
def workspace_file(workspace_id: str, path: str, download: bool = False, view: bool = False):
    try:
        file = workspaces.resolve(workspaces.load(workspace_id), path)
        if view and not download:
            from eegagent.worker.attachments import IMAGE_EXTS, _image_metadata
            from eegagent.worker.store import sha256_of_file
            if file.suffix.lower() in IMAGE_EXTS:
                metadata = _image_metadata(file, sha256_of_file(file))
                return FileResponse(metadata["vision_path"], media_type=metadata["media_type"])
        return FileResponse(file, filename=file.name, content_disposition_type="attachment" if download else "inline")
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.get("/workspaces/{workspace_id}/mentions")
def workspace_mentions(workspace_id: str, query: str = ""):
    try:
        return workspaces.mentions(workspace_id, query)
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.get("/workspaces/{workspace_id}/references")
def workspace_references(workspace_id: str, path: list[str] = Query()):
    try:
        return {"items": workspaces.reference_items(workspace_id, path)}
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.get("/workspaces/{workspace_id}/preview")
def workspace_preview(workspace_id: str, path: str):
    from urllib.parse import quote
    from eegagent.worker.attachments import document_preview, register_attachment, VIDEO_EXTS, DOCUMENT_EXTS, IMAGE_EXTS
    from eegagent.worker.inspect_record import inspect_record
    from eegagent.worker.outputs import plot
    from eegagent.worker.files import EEG_EXTS
    from eegagent.worker.text import TEXT_TABLE_EXTS, classify_text_table, read_text
    try:
        file = workspaces.resolve(workspaces.load(workspace_id), path)
        ext = file.suffix.lower()
        result = {"file_url": f"/workspaces/{workspace_id}/file?path=" + quote(path)}
        if ext in VIDEO_EXTS | DOCUMENT_EXTS | IMAGE_EXTS:
            manifest = register_attachment(str(file))["manifest"]
        elif ext in EEG_EXTS and (ext not in TEXT_TABLE_EXTS or classify_text_table(file) == "signal"):
            manifest = inspect_record(str(file))
            if manifest.get("sample_rate_hz"):
                plotted = plot("raw", manifest["manifest_id"], 0, min(10, manifest["duration_s"]), manifest["channel_names"][:8])
                result["preview"] = plotted["artifact"]
                result["preview_note"] = f"预览前 {len(manifest['channel_names'][:8])} / {manifest['n_channels']} 通道，0–{min(10,manifest['duration_s']):g} 秒。"
            else:
                result["preview_note"] = "采样率尚未确认，当前显示记录结构。"
        else:
            manifest = {"manifest_id": "", "original_name": file.name, "modality": "image" if ext in [".png", ".jpg", ".jpeg", ".svg", ".webp"] else "document", "source_format": ext.lstrip(".")}
        manifest["size_bytes"] = file.stat().st_size
        result["manifest"] = manifest
        if manifest["modality"] == "image":
            result["file_url"] += "&view=1"
        if manifest["modality"] == "document" and manifest["manifest_id"] and ext != ".pdf":
            result.update(document_preview(manifest["manifest_id"]))
        elif ext in [".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".py", ".yaml", ".yml", ".toml"]:
            content = read_text(file, limit=262144)[0]
            result.update(content=content[:65536], content_truncated=len(content)>65536 or file.stat().st_size>262144)
        return result
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.post("/workspaces/{workspace_id}/operations")
def workspace_operation(workspace_id: str, body: WorkspaceOperation):
    try:
        return workspaces.operations(workspace_id, body.action, body.paths, body.destination, body.trash_id)
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


class ToolCall(BaseModel):
    args: dict = {}


@app.on_event("startup")
def _startup() -> None:
    ensure_layout()
    try:
        knowledge.index()
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] knowledge index failed: {exc}")


@app.get("/health")
def health() -> dict:
    capabilities = {"clean_artifacts": CLEANING_METHODS,
                    "unavailable_cleaning_methods": {method: missing for method, missing in CLEANING_MISSING.items() if missing}}
    try:
        ks = knowledge.index().stats()
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "tools": [t["name"] for t in TOOL_DEFINITIONS],
                "knowledge": {"status": "error", "message": str(exc)}, "capabilities": capabilities}
    return {"status": "ok", "tools": [t["name"] for t in TOOL_DEFINITIONS], "knowledge": ks,
            "capabilities": capabilities}


@app.get("/tools")
def tools() -> list[dict]:
    return sorted(TOOL_DEFINITIONS, key=lambda t: t["name"])


@app.get("/models")
def models() -> dict:
    return list_models()


@app.post("/sessions/{session_id}/stop")
def stop_session_script(session_id: str):
    return stop_script(session_id)


@app.post("/tools/{name}")
def call_tool(name: str, body: ToolCall):
    if name not in DISPATCH:
        return JSONResponse({"status": "error", "error": {"code": "unknown_tool", "message": f"unknown tool: {name}", "recoverable": False}}, status_code=404)
    try:
        return run_tool(name, body.args)
    except KeyError as exc:
        return JSONResponse({"status": "error", "error": {"code": "missing_argument", "message": f"Missing field: {exc}", "recoverable": True}}, status_code=400)
    except (FileNotFoundError, PermissionError, ValueError, TypeError) as exc:
        return JSONResponse({"status": "error", "error": {"code": type(exc).__name__, "message": str(exc), "recoverable": True}}, status_code=400)
    except ImportError as exc:
        return JSONResponse({"status": "error", "error": {"code": "dependency_unavailable", "message": f"当前方法的依赖无法载入：{exc}；请改用已安装的方法", "recoverable": True}}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"status": "error", "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}", "recoverable": False,
                                          "trace": traceback.format_exc()[-2000:]}},
            status_code=500,
        )


@app.post("/tools/{name}/events")
def tool_events(name: str, body: ToolCall):
    events = Queue()
    stages = []

    def report(event):
        previous = next((i for i,item in enumerate(stages) if item["stage"]==event["stage"]),None)
        if previous is None:
            stages.append(event)
        else:
            stages[previous] = event
        events.put({"type": "progress", "progress": event})

    def execute():
        with channel(report):
            result = call_tool(name, body)
        if isinstance(result, JSONResponse):
            result = json.loads(result.body)
        result["execution_progress"] = stages
        events.put({"type": "result", "result": result})

    def stream():
        Thread(target=execute, daemon=True).start()
        while True:
            event = events.get()
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] == "result":
                return

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/manifests/{manifest_id}")
def get_manifest(manifest_id: str):
    try:
        return load_manifest(manifest_id)
    except FileNotFoundError as exc:
        return JSONResponse({"status": "error", "error": {"code": "not_found", "message": str(exc)}}, status_code=404)


@app.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    try:
        return load_artifact(artifact_id)
    except FileNotFoundError as exc:
        return JSONResponse({"status": "error", "error": {"code": "not_found", "message": str(exc)}}, status_code=404)


@app.get("/manifests/{manifest_id}/preview")
def preview_document(manifest_id: str):
    from eegagent.worker.attachments import document_preview

    try:
        return document_preview(manifest_id)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/artifacts/{artifact_id}/file")
def get_artifact_file(artifact_id: str):
    from eegagent.worker.outputs import download_filename
    try:
        rec = load_artifact(artifact_id)
        path = resolve_inside_storage(rec["uri"])
        return FileResponse(str(path), media_type=rec.get("media_type") or "application/octet-stream",
                            filename=download_filename(rec, path.suffix))
    except (FileNotFoundError, PermissionError) as exc:
        return JSONResponse({"status": "error", "error": {"code": "not_found", "message": str(exc)}}, status_code=404)


@app.get("/knowledge")
def knowledge_stats():
    return knowledge.index().stats()


@app.get("/knowledge/graph/{entity}")
def knowledge_entity(entity: str):
    return knowledge.knowledge_graph(entity=entity)


@app.get("/knowledge/{chunk_id}")
def knowledge_chunk(chunk_id: str, session_id: str | None = None):
    row = get_document_chunk(chunk_id, session_id) if session_id else None
    if row is None:
        row = knowledge.index().get(chunk_id)
    if row is None:
        return JSONResponse({"status": "error", "error": {"code": "not_found", "message": chunk_id}}, status_code=404)
    return row
