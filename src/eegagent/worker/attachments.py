"""Register uploaded EEG, video, images and documents; read document pages on demand."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pymupdf
from eegagent.core.video_contract import probe_video

from eegagent.worker.files import resolve_readable
from eegagent.worker.store import ensure_layout, load_manifest, save_manifest
from eegagent.worker.text import TEXT_TABLE_EXTS, classify_text_table
from eegagent.knowledge.documents import committed_documents, document_dir, document_text, parse_document, load_document

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
DOCUMENT_EXTS = {".pdf", ".md", ".markdown", ".docx", ".json", ".yaml", ".yml", ".rst"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
UNSUPPORTED_HINTS = {".xlsx": "Excel 表格请另存为 CSV 后上传", ".xls": "Excel 表格请另存为 CSV 后上传",
                     ".doc": "旧版 Word 文档请另存为 .docx 或 PDF 后上传"}


def _video_metadata(path: Path) -> tuple[dict, list[str]]:
    metadata = probe_video(path)
    return metadata, metadata.pop("warnings")


def _image_metadata(path: Path, digest: str) -> dict:
    from PIL import Image, ImageOps

    with Image.open(path) as image:
        image.load()
        oriented = ImageOps.exif_transpose(image)
        vision_path = path
        media_type = Image.MIME[image.format]
        if image.format not in {"PNG", "JPEG", "WEBP"} or image.getexif().get(274, 1) != 1:
            directory = ensure_layout() / "uploads" / "image_inputs"
            directory.mkdir(exist_ok=True)
            vision_path = directory / f"{digest}.png"
            oriented.convert("RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB").save(vision_path, format="PNG")
            media_type = "image/png"
        return {"width": oriented.width, "height": oriented.height, "media_type": media_type,
                "vision_path": str(vision_path), "frame_count": getattr(image, "n_frames", 1)}


def register_attachment(path: str, session_id: str | None = None) -> dict:
    file = resolve_readable(path)
    ext = file.suffix.lower()
    if ext not in VIDEO_EXTS | DOCUMENT_EXTS | IMAGE_EXTS | TEXT_TABLE_EXTS:
        raise ValueError(UNSUPPORTED_HINTS.get(ext, f"不支持的附件格式：{ext}"))
    digest = hashlib.sha256()
    with file.open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(block)
    modality = "video" if ext in VIDEO_EXTS else "image" if ext in IMAGE_EXTS else "document"
    prefix = {"video": "vid", "image": "img", "document": "doc"}[modality]
    manifest = {"manifest_id": f"{prefix}_{uuid.uuid4().hex[:8]}",
                "modality": modality, "attachment_sha256": digest.hexdigest(), "original_name": file.name,
                "source_path": str(file), "source_format": ext.lstrip("."), "size_bytes": file.stat().st_size,
                "warnings": []}
    if modality == "video":
        metadata, warnings = _video_metadata(file)
        manifest.update(metadata)
        manifest["warnings"] = warnings
        manifest["role"] = {"value": None, "source": "unknown"}
        manifest["compat"] = []
    elif modality == "image":
        manifest.update(_image_metadata(file, digest.hexdigest()))
        manifest["compat"] = []
    else:
        document = parse_document(file, manifest["manifest_id"], session_id)
        manifest["page_count"] = document["page_count"]
        manifest["document_index"] = document
        if document["pages_needing_ocr"]:
            manifest["warnings"].append("部分页面只有图像，需要 OCR 后才能检索这些页面")
    save_manifest(manifest)
    return {"status": "ok", "manifest": manifest}


def register_upload(path: str, session_id: str | None = None, sample_rate_hz: float | None = None,
                    channel_names: list[str] | None = None, trial_index: int | None = None) -> dict:
    """Route one uploaded file by its content: video and documents are attachments, signal files become EEG records.

    Delimited text is an EEG record only when it holds a long numeric signal table or the uploader supplied
    acquisition facts; labels, events and notes stay readable documents.
    """
    from eegagent.worker.inspect_record import inspect_record

    file = resolve_readable(path)
    ext = file.suffix.lower()
    if ext in VIDEO_EXTS | DOCUMENT_EXTS | IMAGE_EXTS:
        return register_attachment(str(file), session_id)
    if ext in TEXT_TABLE_EXTS and classify_text_table(file, sample_rate_hz=sample_rate_hz, channel_names=channel_names) != "signal":
        return register_attachment(str(file), session_id)
    if ext in UNSUPPORTED_HINTS:
        raise ValueError(UNSUPPORTED_HINTS[ext])
    manifest = inspect_record(str(file), sample_rate_hz=sample_rate_hz, channel_names=channel_names, trial_index=trial_index)
    return {"status": "ok", "manifest": manifest}


def document_preview(manifest_id: str) -> dict:
    """Preview a registered draft or submitted document using the same parsed text as retrieval."""
    manifest = load_manifest(manifest_id)
    if manifest["modality"] != "document":
        raise ValueError("该附件不是文档")
    resolve_readable(manifest["source_path"])
    if "document_index" in manifest:
        parsed = load_document(manifest_id, manifest["document_index"]["session_id"])
        text = "\n\n".join(part for page in parsed["pages"] for part in [page["text"], *page["tables"]])
    else:
        text = document_text(Path(manifest["source_path"]))
    limit = 64 * 1024
    return {"content": text[:limit], "content_truncated": len(text) > limit}


def read_document(manifest_id: str, page: int = 1, limit: int = 3, offset: int = 0,
                  session_id: str | None = None) -> dict:
    manifest = load_manifest(manifest_id)
    if manifest["modality"] != "document":
        raise ValueError("该附件不是文档")
    if session_id is not None and manifest_id not in committed_documents(session_id):
        raise PermissionError("该文档尚未提交到本会话")
    file = resolve_readable(manifest["source_path"])
    parsed = None
    if "document_index" in manifest:
        read_session = session_id if session_id is not None else manifest["document_index"]["session_id"]
        parsed = load_document(manifest_id, read_session)
        start = max(0, int(page) - 1)
        selected = parsed["pages"][start:start + min(int(limit), 5)]
        pages = [{"page": row["page"], "text": "\n\n".join([row["text"], *row["tables"]]).strip(),
                  "needs_ocr": row["needs_ocr"],
                  "extraction_method": "native_text_and_tables" if row["tables"] else "native_text"} for row in selected]
        total = len(parsed["pages"])
    elif file.suffix.lower() == ".pdf":
        with pymupdf.open(file) as pdf:
            start = max(0, int(page) - 1)
            end = min(len(pdf), start + min(int(limit), 5))
            pages = [{"page": i + 1, "text": pdf[i].get_text()} for i in range(start, end)]
            total = len(pdf)
    else:
        text = document_text(file)
        pages = [{"page": 1, "text": text}]
        total = 1
    budget = 2000 // max(1, len(pages))
    chunks = []
    for row in pages:
        text = row["text"]
        row["text"] = text[offset:offset + budget]
        row["start_offset"] = offset
        row["next_offset"] = offset + len(row["text"])
        row["has_more"] = row["next_offset"] < len(text)
        if row["text"].strip():
            digest = hashlib.sha256(f"{manifest_id}|{row['page']}|{offset}|{row['text']}".encode()).hexdigest()
            row["chunk_id"] = "k_" + digest[:12]
            chunks.append({"chunk_id": row["chunk_id"], "doc_id": manifest_id, "manifest_id": manifest_id,
                           "title": manifest["original_name"], "section": f"第 {row['page']} 页", "page": row["page"],
                           "start_offset": offset, "next_offset": row["next_offset"], "text": row["text"],
                           "source": f"{manifest['original_name']} · 第 {row['page']} 页",
                           "scope": "session", "doc_type": "document", "source_kind": "user_upload",
                           "extraction_method": row.get("extraction_method", "native_text"),
                           "evidence_role": "document_claim", "content_sha256": hashlib.sha256(row["text"].encode()).hexdigest()})
    if parsed is not None and chunks:
        read_chunks = parsed.setdefault("read_chunks", {})
        read_chunks.update({chunk["chunk_id"]: chunk for chunk in chunks})
        (document_dir(read_session) / f"{manifest_id}.json").write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "manifest_id": manifest_id, "pages": pages, "chunks": chunks, "total_pages": total,
            "note": "用返回的 [chunk_id] 引用实际读取的段落；has_more=true 时以 next_offset 继续。未读取内容不能由页码推定。"}
