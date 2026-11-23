"""Parse uploaded documents once and retrieve page-cited chunks within a session."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import numpy as np
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


OCR_MAX_PAGES = 60


@functools.lru_cache(maxsize=None)
def ocr_language() -> str | None:
    """Tesseract languages for scanned pages, or None when OCR is disabled or unavailable."""
    if os.environ.get("EEGAGENT_OCR", "").lower() in ("off", "0", "false"):
        return None
    if lang := os.environ.get("EEGAGENT_OCR_LANG"):
        return lang
    try:
        tessdata = Path(pymupdf.get_tessdata())
    except Exception:
        return None
    names = [name for name in ("chi_sim", "eng") if (tessdata / f"{name}.traineddata").exists()]
    return "+".join(names) or None


def parse_document(path: Path, manifest_id: str, session_id: str | None) -> dict:
    pages = []
    lang, lang_resolved, ocr_done = None, False, 0
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
                needs_ocr, ocr = not text.strip() and not table_text and has_images, False
                if needs_ocr and not lang_resolved:
                    lang, lang_resolved = ocr_language(), True
                if needs_ocr and lang and ocr_done < OCR_MAX_PAGES:
                    ocr_done += 1
                    try:
                        textpage = page.get_textpage_ocr(flags=0, language=lang, dpi=200, full=True)
                        ocr_text = page.get_text("text", textpage=textpage).strip()
                    except Exception:
                        ocr_text = ""
                    if ocr_text:
                        text, needs_ocr, ocr = ocr_text, False, True
                pages.append({"page": number, "text": text, "tables": table_text, "has_images": has_images,
                              "needs_ocr": needs_ocr, "ocr": ocr})
    else:
        pages = [{"page": 1, "text": document_text(path), "tables": [], "has_images": False, "needs_ocr": False, "ocr": False}]

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
                                   "extraction_method": "native_table" if kind == "table" else "ocr_text" if page.get("ocr") else "native_text",
                                   "content_sha256": hashlib.sha256(piece.encode("utf-8")).hexdigest()})
    missing = [page["page"] for page in pages if page["needs_ocr"]]
    status = "partial" if missing and chunks else "ocr_required" if missing else "ready" if chunks else "empty"
    summary = {"status": status, "n_chunks": len(chunks), "page_count": len(pages),
               "pages_needing_ocr": missing, "pages_with_images": [page["page"] for page in pages if page["has_images"]],
               "ocr_pages": [page["page"] for page in pages if page["ocr"]], "ocr_language": lang or None,
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


_shared_embedder: object = False  # False = unresolved, None = configured off
_document_dense_down_until = 0.0
_vec_jobs: set[tuple[str | None, str]] = set()
_vec_jobs_lock = threading.Lock()


def shared_embedder():
    """Process-wide optional embedder for uploaded-document retrieval; None when unconfigured."""
    global _shared_embedder
    if _shared_embedder is False:
        from .embed import make_embedder
        _shared_embedder = make_embedder()
    return _shared_embedder or None


def reset_shared_embedder() -> None:
    global _shared_embedder, _document_dense_down_until
    _shared_embedder = False
    _document_dense_down_until = 0.0


def _vectors_path(manifest_id: str, session_id: str | None) -> Path:
    return document_dir(session_id) / f"{manifest_id}.vec.npz"


def document_vectors(manifest_id: str, session_id: str | None):
    """(chunk_ids, vectors) from the sidecar when it matches the configured embedder, else None."""
    embedder = shared_embedder()
    path = _vectors_path(manifest_id, session_id)
    if embedder is None or not path.exists():
        return None
    try:
        with np.load(path) as z:
            if str(z["embedder"].item()) != embedder.name:
                return None
            ids, vectors = z["ids"], np.asarray(z["vectors"], dtype=np.float32)
        if (ids.ndim != 1 or vectors.ndim != 2 or vectors.shape[0] != ids.shape[0]
                or vectors.shape[1] == 0 or not np.isfinite(vectors).all()):
            return None
        if len({str(i) for i in ids}) != len(ids):
            return None
        chunk_ids = {c["chunk_id"] for c in load_document(manifest_id, session_id)["chunks"]}
        if not chunk_ids <= {str(i) for i in ids}:
            return None
        return ids, vectors
    except Exception:  # noqa: BLE001
        return None


def embed_document(manifest_id: str, session_id: str | None) -> Path | None:
    """Embed every chunk of a parsed document and write the sidecar atomically."""
    embedder = shared_embedder()
    if embedder is None:
        return None
    chunks = load_document(manifest_id, session_id)["chunks"]
    if not chunks:
        return None
    texts = [f"{c['title']} › {c['section']}\n{c['text']}" for c in chunks]
    vectors = [embedder.encode(texts[i:i + 32]) for i in range(0, len(texts), 32)]
    matrix = np.asarray(np.vstack(vectors), dtype=np.float32)
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
    target = _vectors_path(manifest_id, session_id)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("wb") as f:
        np.savez(f, ids=np.array([c["chunk_id"] for c in chunks]), vectors=matrix,
                 embedder=np.array(embedder.name))
    os.replace(tmp, target)
    return target


def ensure_document_vectors(manifest_id: str, session_id: str | None) -> None:
    """Start a daemon job embedding a document whose sidecar is absent or stale."""
    if shared_embedder() is None or document_vectors(manifest_id, session_id) is not None:
        return
    try:
        if not load_document(manifest_id, session_id)["chunks"]:
            return
    except Exception:  # noqa: BLE001
        return
    key = (session_id, manifest_id)
    with _vec_jobs_lock:
        if key in _vec_jobs:
            return
        _vec_jobs.add(key)

    def job() -> None:
        try:
            embed_document(manifest_id, session_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge] document embedding failed {manifest_id}: {exc}")
        finally:
            with _vec_jobs_lock:
                _vec_jobs.discard(key)

    threading.Thread(target=job, daemon=True).start()


def _dense_document_scores(query: str, chunks: list[dict], session_id: str | None) -> list[tuple[int, float]] | None:
    """Top-50 (chunk position, similarity) over vectorised documents, or None when dense is unavailable."""
    global _document_dense_down_until
    if time.monotonic() < _document_dense_down_until:
        return None
    sidecars = [loaded for mid in committed_documents(session_id)
                if (loaded := document_vectors(mid, session_id)) is not None]
    if not sidecars:
        return None
    embedder = shared_embedder()
    try:
        qv = np.asarray(embedder.encode_query(query), dtype=np.float32)
        if qv.ndim != 1 or not np.isfinite(qv).all() or any(vectors.shape[1] != qv.shape[0] for _, vectors in sidecars):
            raise ValueError("document query embedding has invalid shape or values")
    except Exception as exc:  # noqa: BLE001
        _document_dense_down_until = time.monotonic() + 60
        print(f"[knowledge] document dense retrieval unavailable: {exc}")
        return None
    pos = {row["chunk_id"]: i for i, row in enumerate(chunks)}
    sims: list[tuple[int, float]] = []
    for ids, vectors in sidecars:
        scores = vectors @ qv
        for j in range(len(ids)):
            i = pos.get(str(ids[j]))
            if i is not None and scores[j] > 0:
                sims.append((i, float(scores[j])))
    if not sims:
        return None
    sims.sort(key=lambda x: -x[1])
    return sims[:50]


def search_documents(query: str, session_id: str | None, k: int) -> list[dict]:
    chunks = session_chunks(session_id)
    if not chunks:
        return []
    tokens = [tokenize(f"{row['title']} {row['section']} {row['text']}") for row in chunks]
    sparse = BM25(tokens).top(tokenize(query), 50)
    if shared_embedder() is None:
        return [{**chunks[index], "score": round(score, 4)} for index, score in sparse[:k]]
    for manifest_id in committed_documents(session_id):
        ensure_document_vectors(manifest_id, session_id)
    dense = _dense_document_scores(query, chunks, session_id)
    if dense is None:
        return [{**chunks[index], "score": round(score, 4)} for index, score in sparse[:k]]
    fused: dict[int, float] = {}
    for ranked in (sparse, dense):
        for rank, (index, _) in enumerate(ranked):
            fused[index] = fused.get(index, 0.0) + 1.0 / (60 + rank + 1)
    return [{**chunks[index], "score": round(fused[index], 4)}
            for index in sorted(fused, key=lambda i: -fused[i])[:k]]


def get_document_chunk(chunk_id: str, session_id: str) -> dict | None:
    for manifest_id in committed_documents(session_id):
        parsed = load_document(manifest_id, session_id)
        for row in [*parsed["chunks"], *parsed.get("read_chunks", {}).values()]:
            if row["chunk_id"] == chunk_id:
                return row
    return None
