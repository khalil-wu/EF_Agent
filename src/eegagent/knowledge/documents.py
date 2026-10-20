"""Parse uploaded documents once and retrieve page-cited chunks within a session."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pymupdf

from .bm25 import BM25
from .chunking import _split_long, markdown_sections
from .tokenize import tokenize


def document_dir(session_id: str | None) -> Path:
    root = Path(os.environ.get("EEGAGENT_STORAGE_DIR", Path(__file__).resolve().parents[3] / "storage"))
    return root / "documents" / (session_id or "unscoped")


def _docx_text(path: Path) -> str:
    """Paragraphs and table rows of a .docx in reading order (Office Open XML, no extra dependency)."""
    import zipfile
    from xml.etree import ElementTree

    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))

    def text_of(element) -> str:
        return "".join(node.text or "" for node in element.iter(word + "t")).strip()

    lines = []
    body = root.find(word + "body")
    for block in (body if body is not None else []):
        if block.tag == word + "tbl":
            for row in block.iter(word + "tr"):
                cells = [text_of(cell) for cell in row.iter(word + "tc")]
                if any(cells):
                    lines.append(" | ".join(cells))
        else:
            for paragraph in ([block] if block.tag == word + "p" else block.iter(word + "p")):
                if text := text_of(paragraph):
                    lines.append(text)
    return "\n".join(lines)


def document_text(path: Path) -> str:
    """Text of a single-page document; text files are decoded as UTF-8 or GB18030 regardless of the host locale."""
    from eegagent.worker.text import read_text

    if path.suffix.lower() == ".docx":
        return _docx_text(path)
    return read_text(path)[0]


def parse_document(path: Path, manifest_id: str, session_id: str | None) -> dict:
    pages = []
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as pdf:
            for number, page in enumerate(pdf, 1):
                tables = page.find_tables().tables
                areas = [pymupdf.Rect(table.bbox) for table in tables]
                blocks = [block for block in page.get_text("blocks", sort=True) if block[6] == 0]
                paragraphs = [block[4].strip() for block in blocks
                              if not any(area.contains((pymupdf.Rect(block[:4]).tl + pymupdf.Rect(block[:4]).br) / 2) for area in areas)]
                text = "\n\n".join(paragraphs)
                table_text = [table.to_markdown() for table in tables]
                has_images = bool(page.get_images())
                pages.append({"page": number, "text": text, "tables": table_text, "has_images": has_images,
                              "needs_ocr": not text.strip() and not table_text and has_images})
    else:
        pages = [{"page": 1, "text": document_text(path), "tables": [], "has_images": False, "needs_ocr": False}]

    chunks = []
    for page in pages:
        # Keep page boundaries and headings; tables retain their own provenance.
        for kind, body in [("text", page["text"]), *(("table", table) for table in page["tables"])]:
            sections = markdown_sections(body, levels=range(1, 7))
            for section in sections:
                section = section.strip()
                if not section.strip():
                    continue
                heading = section.splitlines()[0].lstrip("# ") if section.startswith("#") else f"第 {page['page']} 页"
                for piece in _split_long(section.strip()):
                    chunk_id = "k_" + hashlib.sha256(f"{manifest_id}|{page['page']}|{kind}|{len(chunks)}|{piece}".encode()).hexdigest()[:12]
                    chunks.append({"chunk_id": chunk_id, "doc_id": manifest_id, "title": path.name,
                                   "section": heading, "page": page["page"], "kind": kind,
                                   "text": piece, "doc_type": "document", "scope": "session",
                                   "source": f"{path.name} · 第 {page['page']} 页", "manifest_id": manifest_id,
                                   "evidence_role": "document_claim", "source_kind": "user_upload",
                                   "extraction_method": "native_table" if kind == "table" else "native_text",
                                   "content_sha256": hashlib.sha256(piece.encode("utf-8")).hexdigest()})
    missing = [page["page"] for page in pages if page["needs_ocr"]]
    status = "partial" if missing and chunks else "ocr_required" if missing else "ready" if chunks else "empty"
    summary = {"status": status, "n_chunks": len(chunks), "page_count": len(pages),
               "pages_needing_ocr": missing, "pages_with_images": [page["page"] for page in pages if page["has_images"]],
               "image_content_extracted": False, "session_id": session_id}
    parsed = {"summary": summary, "pages": pages, "chunks": chunks}
    directory = document_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{manifest_id}.json").write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    return summary


def load_document(manifest_id: str, session_id: str | None) -> dict:
    return json.loads((document_dir(session_id) / f"{manifest_id}.json").read_text(encoding="utf-8"))


def committed_documents(session_id: str | None) -> list[str]:
    """The saved conversation's submitted attachments are the retrieval authority."""
    if not session_id:
        return []
    snapshot = document_dir(session_id).parents[1] / "sessions" / f"{session_id}.json"
    if not snapshot.exists():
        return []  # A registered draft has not been submitted to a conversation.
    session = json.loads(snapshot.read_text(encoding="utf-8"))
    return list(dict.fromkeys(row["manifest_id"] for row in (session["manifests"] or [])
                             if row["modality"] == "document"))


def session_chunks(session_id: str | None) -> list[dict]:
    return [chunk for manifest_id in committed_documents(session_id)
            for chunk in load_document(manifest_id, session_id)["chunks"]]


def search_documents(query: str, session_id: str | None, k: int) -> list[dict]:
    chunks = session_chunks(session_id)
    tokens = [tokenize(f"{row['title']} {row['section']} {row['text']}") for row in chunks]
    return [{**chunks[index], "score": round(score, 4)} for index, score in BM25(tokens).top(tokenize(query), k)]


def get_document_chunk(chunk_id: str, session_id: str) -> dict | None:
    for manifest_id in committed_documents(session_id):
        parsed = load_document(manifest_id, session_id)
        for row in [*parsed["chunks"], *parsed.get("read_chunks", {}).values()]:
            if row["chunk_id"] == chunk_id:
                return row
    return None
