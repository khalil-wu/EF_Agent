"""Markdown → chunks. Notes and papers share the same format: frontmatter + '## ' sections.

Chunk text used for indexing is prefixed with "title › section" (cheap contextual retrieval) and, if a
sidecar <doc>.ctx.json exists (LLM-generated chunk context), with that sentence too.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from .tokenize import tokenize

MAX_CHUNK_CHARS = 1800


def knowledge_dir() -> Path:
    return Path(os.environ.get("EEGAGENT_KNOWLEDGE_DIR", Path(__file__).resolve().parents[3] / "knowledge"))


def split_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        if ":" not in lines[i]:
            continue
        k, v = lines[i].split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k.strip()] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k.strip()] = v.strip("'\"")
    return {}, text


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    path: str
    doc_type: str = "note"  # note | paper
    source: str = ""
    version: str = ""
    domain: str = ""
    tasks: list[str] = field(default_factory=list)
    license: str = ""
    meta: dict = field(default_factory=dict)
    context: str = ""
    document_text: str = ""
    document_sha256: str = ""
    entities: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    source_info: dict = field(default_factory=dict)
    parsed_start_offset: int = 0
    source_refs: list[str] = field(default_factory=list)
    source_labels: dict[str, str] = field(default_factory=dict)

    @property
    def index_text(self) -> str:
        head = f"{self.title} › {self.section}"
        return "\n".join(x for x in (head, self.context, self.text) if x)

    @property
    def text_sha(self) -> str:
        return hashlib.sha1(self.index_text.encode("utf-8")).hexdigest()

    def row(self, score: float | None = None) -> dict:
        r = {"chunk_id": self.chunk_id, "doc_id": self.doc_id, "title": self.title, "section": self.section,
             "doc_type": self.doc_type, "source": self.source, "version": self.version, "domain": self.domain,
             "tasks": self.tasks, "license": self.license, "text": self.text, "entities": self.entities,
             "evidence_role": "reference", "source_kind": {"paper": "paper_excerpt", "documentation": "official_documentation"}.get(self.doc_type, "project_note"),
             "content_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest()}
        if self.document_text:
            directory = Path(os.environ.get("EEGAGENT_STORAGE_DIR", Path(__file__).resolve().parents[3] / "storage")) / "knowledge_sources"
            directory.mkdir(parents=True, exist_ok=True)
            original = directory / f"{self.document_sha256}.md"
            if not original.exists():
                original.write_bytes(self.document_text.encode("utf-8"))
            r["document_sha256"] = self.document_sha256
            r["document_name"] = Path(self.path).name
            r["original_url"] = f"/knowledge/documents/{self.document_sha256}/file"
            if self.source_info:
                source_sha = self.source_info["sha256"]
                source_format = self.source_info["format"]
                source_file = directory / f"{source_sha}.{source_format}"
                if not source_file.exists():
                    shutil.copyfile(self.source_info["path"], source_file)
                r.update(source_document_sha256=source_sha, source_document_name=self.source_info["name"],
                         source_original_url=f"/knowledge/documents/{source_sha}/file?format={source_format}",
                         extraction_method=self.source_info["extraction_method"],
                         image_content_extracted=self.source_info["image_content_extracted"])
                r.update(parsed_start_offset=self.parsed_start_offset, parsed_end_offset=self.parsed_start_offset + len(self.text))
                page = re.fullmatch(r"第 (\d+) 页", self.section)
                if page and source_format == "pdf":
                    r["page"] = int(page[1])
                    if r["page"] in self.source_info["untranscribed_formula_pages"]:
                        r["parse_note"] = "该页含未转写的数学公式，相关公式须查看原 PDF；附近文字不能作为公式原文。"
                    if r["page"] in self.source_info["verified_formula_pages"]:
                        r["page_has_verified_formula_transcription"] = True
        if self.meta:
            r["meta"] = self.meta
        if score is not None:
            r["score"] = score
        return r


def _split_long(section_text: str) -> list[str]:
    if len(section_text) <= MAX_CHUNK_CHARS:
        return [section_text]
    out: list[str] = []
    remaining = section_text
    while len(remaining) > MAX_CHUNK_CHARS:
        # Slice the original text rather than reconstructing paragraph separators.
        # Each excerpt must remain an exact substring of its document snapshot.
        paragraph = remaining.rfind("\n\n", 0, MAX_CHUNK_CHARS)
        if paragraph > MAX_CHUNK_CHARS // 2:
            cut = paragraph + 2
        else:
            boundary = max(remaining.rfind("\n", 0, MAX_CHUNK_CHARS),
                           remaining.rfind("。", 0, MAX_CHUNK_CHARS),
                           remaining.rfind(". ", 0, MAX_CHUNK_CHARS))
            cut = boundary + 1 if boundary > MAX_CHUNK_CHARS // 2 else MAX_CHUNK_CHARS
        out.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        out.append(remaining)
    return out


def markdown_sections(text: str, levels=(2,)) -> list[str]:
    """Split actual headings while retaining fenced source excerpts verbatim."""
    sections, start, offset = [], 0, 0
    fence_char, fence_length = "", 0
    for line in text.splitlines(keepends=True):
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            marker, suffix = fence.groups()
            if not fence_char:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length and not suffix.strip():
                fence_char, fence_length = "", 0
        elif not fence_char:
            heading = re.match(r"^ {0,3}(#{1,6})\s", line)
            if heading and len(heading[1]) in levels and offset > start:
                sections.append(text[start:offset])
                start = offset
        offset += len(line)
    if text[start:]:
        sections.append(text[start:])
    return sections


def chunk_markdown(path: Path, doc_type: str = "note") -> list[Chunk]:
    document_text = path.read_text(encoding="utf-8")
    document_sha256 = hashlib.sha256(document_text.encode("utf-8")).hexdigest()
    meta, body = split_frontmatter(document_text)
    doc_id = str(meta.get("doc_id") or path.stem)
    title = str(meta.get("title") or path.stem)
    tasks = meta.get("tasks") or []
    if isinstance(tasks, str):
        tasks = [tasks]
    extra = {k: v for k, v in meta.items() if k in ("year", "authors", "doi", "venue", "url", "arxiv")}
    ctx: dict = {}
    source_side = path.with_suffix(".source.json")
    source_info = json.loads(source_side.read_text(encoding="utf-8")) if source_side.exists() else {}
    side = path.with_suffix(".ctx.json")
    if side.exists():
        try:
            ctx = json.loads(side.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            ctx = {}
    chunks: list[Chunk] = []
    cursor = document_text.index(body)
    parts = markdown_sections(body)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        first = part.splitlines()[0]
        heading = re.match(r"^ {0,3}##\s+(.+)", first)
        section = heading[1].strip() if heading else "（引言）"
        for piece in _split_long(part):
            parsed_start = document_text.index(piece, cursor)
            cursor = parsed_start + len(piece)
            identity = f"{document_sha256}|{doc_id}|{section}|{piece}|{meta.get('doc_type') or doc_type}"
            if source_info:
                identity += "|" + source_info["sha256"] + "|" + str(parsed_start)
            cid = "k_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
            c = Chunk(cid, doc_id, title, section, piece, str(path), doc_type=str(meta.get("doc_type") or doc_type),
                      source=str(meta.get("source", "")), version=str(meta.get("version", "")),
                      domain=str(meta.get("domain", "")), tasks=[str(t) for t in tasks],
                      license=str(meta.get("license", "")), meta=extra, context=str(ctx.get(cid, "")),
                      document_text=document_text, document_sha256=document_sha256, source_info=source_info, parsed_start_offset=parsed_start,
                      source_refs=[f"{doc}#{unquote(section)}" for doc, section in re.findall(
                          r"\]\((?:\.\./)+(?:papers|references)/([\w-]+)\.md#([^\)]+)\)", part)],
                      source_labels={f"{doc}#{unquote(section)}": label for label, doc, section in re.findall(
                          r"\[([^\]]+)\]\((?:\.\./)+(?:papers|references)/([\w-]+)\.md#([^\)]+)\)", part)})
            c.tokens = tokenize(c.index_text)
            chunks.append(c)
    return chunks


def load_all_chunks(root: Path | None = None) -> list[Chunk]:
    root = root or knowledge_dir()
    out: list[Chunk] = []
    if not root.exists():
        return out
    for path in sorted(root.rglob("*.md")):
        if path.name.lower() in ("readme.md", "papers.md"):
            continue
        rel = path.relative_to(root).parts
        if rel and rel[0] in ("graph",):
            continue
        doc_type = "paper" if (rel and rel[0] == "papers") else "note"
        out.extend(chunk_markdown(path, doc_type))
    return out
