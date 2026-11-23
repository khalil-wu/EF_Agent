"""Locate explicit note citations without calling a model or treating a match as scientific proof."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from .chunking import knowledge_dir, load_all_chunks
from .graph import KnowledgeGraph


def normalized_excerpt(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def audit_sources(root: Path | None = None) -> dict:
    root = root or knowledge_dir()
    chunks = load_all_chunks(root)
    graph = KnowledgeGraph(root, chunks)
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    doc_ids = {chunk.doc_id for chunk in chunks}
    bindings = []
    for note in chunks:
        if note.doc_type in ("paper", "documentation"):
            continue
        for ref in dict.fromkeys(note.source_refs):
            section_ref, _, quote = ref.partition(":~:text=")
            doc_id = section_ref.partition("#")[0]
            candidates = [by_id[cid] for cid in graph.resolve_ref(section_ref)]
            primary = [chunk for chunk in candidates if chunk.doc_type in ("paper", "documentation")]
            located = [chunk.chunk_id for chunk in primary
                       if not quote or normalized_excerpt(quote) in normalized_excerpt(chunk.text)]
            if doc_id not in doc_ids:
                status = "missing_document"
            elif not candidates:
                status = "missing_section"
            elif not primary:
                status = "not_primary_source"
            elif not located:
                status = "missing_quote"
            else:
                status = "located"
            bindings.append({
                "note_doc_id": note.doc_id,
                "note_section": note.section,
                "note_chunk_id": note.chunk_id,
                "source_ref": ref,
                "quote_anchored": bool(quote),
                "status": status,
                "source_chunk_ids": located,
            })
    return {
        "n_bindings": len(bindings),
        "n_located": sum(row["status"] == "located" for row in bindings),
        "n_unresolved": sum(row["status"] != "located" for row in bindings),
        "n_quote_anchored": sum(row["quote_anchored"] for row in bindings),
        "bindings": bindings,
        "note": "A located excerpt proves text availability, not that the source supports every claim or that an EEG result is valid.",
    }
