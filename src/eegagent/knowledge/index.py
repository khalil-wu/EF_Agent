"""KnowledgeIndex: chunks (notes + papers) + BM25 + optional dense vectors + graph; hybrid retrieval with
RRF fusion of three retrievers (BM25, dense, entity-linked defining chunks), optional cross-encoder rerank, and
1-hop graph expansion (LightRAG-style dual level: text chunks + relation facts).
"""

from __future__ import annotations

from pathlib import Path
import unicodedata

import numpy as np

from .bm25 import BM25
from .chunking import Chunk, knowledge_dir, load_all_chunks
from .embed import BaseEmbedder, Reranker, embed_chunks, make_embedder, make_reranker
from .graph import KnowledgeGraph
from .tokenize import tokenize

RRF_K = 60
CAND = 50
GRAPH_SUPPORT_LIMIT = 2
FACT_LIMIT = 6
SOURCE_SUPPORT_LIMIT = 2


def merge_sources(rows: list[dict], sources: list[dict]) -> list[dict]:
    """Deliver an original excerpt before the note that links to it, without duplicating matches."""
    present = {row["chunk_id"] for row in rows}
    out = []
    for row in rows:
        for source in sources:
            if row["chunk_id"] in source["supports_notes"] and source["chunk_id"] not in present:
                out.append(source)
                present.add(source["chunk_id"])
        out.append(row)
    return out


def bundle_facts(rows: list[dict], facts: list[dict], by_id: dict[str, Chunk],
                 support_limit: int = GRAPH_SUPPORT_LIMIT, fact_limit: int = FACT_LIMIT) -> dict:
    """Select relations with readable evidence; support chunks have their own small budget."""
    chunks = list(rows)
    have = {row["chunk_id"] for row in chunks if row["text"].strip()}
    selected = []
    added = 0
    for fact in facts:
        evidence = [cid for cid in fact["evidence"] if cid in have]
        if not evidence and added < support_limit:
            for cid in fact["evidence"]:
                chunk = by_id[cid]
                if chunk.text.strip():
                    chunks.append({**chunk.row(0.0), "retrieval_role": "graph_support"})
                    have.add(cid)
                    evidence = [cid]
                    added += 1
                    break
        if evidence:
            selected.append({**fact, "evidence": evidence})
            if len(selected) >= fact_limit:
                break
    return {"chunks": chunks, "facts": selected}


class KnowledgeIndex:
    def __init__(self, root: Path | None = None, embedder: BaseEmbedder | None | str = "auto",
                 reranker: Reranker | None | str = "auto", use_cache: bool = True, include_proposed: bool | None = None):
        self.root = root or knowledge_dir()
        self.chunks: list[Chunk] = load_all_chunks(self.root)
        self.by_id = {c.chunk_id: c for c in self.chunks}
        self._pos = {c.chunk_id: i for i, c in enumerate(self.chunks)}
        self.graph = KnowledgeGraph(self.root, self.chunks, include_proposed)
        self.graph.link_chunks(self.chunks)
        self.bm25 = BM25([c.tokens for c in self.chunks])
        self.embedder = make_embedder() if embedder == "auto" else embedder
        self.reranker = make_reranker() if reranker == "auto" else reranker
        self.vectors = embed_chunks(self.embedder, self.chunks, use_cache) if self.embedder else None

    # ------------------------------------------------------------ retrieval

    def _dense_top(self, query: str, n: int) -> list[tuple[int, float]]:
        if self.vectors is None or self.embedder is None:
            return []
        qv = self.embedder.encode([query])[0]
        sims = self.vectors @ qv
        order = np.argsort(-sims)[:n]
        return [(int(i), float(sims[i])) for i in order if sims[i] > 0]

    def search(self, query: str, k: int = 5, domain: str | None = None, tasks: list[str] | None = None,
               doc_type: str | None = None, expand_graph: bool = True) -> dict:
        if not self.chunks:
            return {"chunks": [], "facts": [], "entities": []}
        q = tokenize(query)
        bm = self.bm25.top(q, CAND)
        de = self._dense_top(query, CAND)
        use_graph = expand_graph and bool(self.graph.entities)
        q_ents = self.graph.link_text(query, limit=6) if use_graph else []
        # third retriever (entity level): the chunks that define each entity named in the query
        ent: list[int] = []
        for eid in q_ents:
            for cid in self.graph.entities[eid].chunk_ids[:2]:
                i = self._pos.get(cid)
                if i is not None and i not in ent:
                    ent.append(i)
        fused: dict[int, float] = {}
        for ranked in (bm, de, [(i, 0.0) for i in ent]):
            for rank, (i, _) in enumerate(ranked):
                fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
        order = sorted(fused, key=lambda i: -fused[i])

        def ok(c: Chunk) -> bool:
            if domain and c.domain != domain:
                return False
            if tasks and not (set(tasks) & set(c.tasks)):
                return False
            if doc_type and c.doc_type != doc_type:
                return False
            return True

        cands = [i for i in order if ok(self.chunks[i])][: max(k * 4, 20)]
        scores = {i: fused[i] for i in cands}
        if self.reranker and cands:
            rs = self.reranker.scores(query, [self.chunks[i].index_text for i in cands])
            cands = [i for _, i in sorted(zip(rs, cands), key=lambda x: -x[0])]
            scores = {i: float(s) for s, i in zip(sorted(rs, reverse=True), cands)}
        top = cands[: max(1, min(int(k), 10))]
        rows = [{**self.chunks[i].row(round(scores[i], 4)), "retrieval_role": "match"} for i in top]

        linked_sources = []
        for note_rank, row in enumerate(rows):
            note = self.by_id[row["chunk_id"]]
            for ref in note.source_refs:
                section_ref, _, quote = ref.partition(":~:text=")
                candidates = [self.by_id[cid] for cid in self.graph.resolve_ref(section_ref)
                              if self.by_id[cid].doc_type in ("paper", "documentation") and ok(self.by_id[cid])]
                if quote:
                    normalized_quote = " ".join(unicodedata.normalize("NFKC", quote).split()).casefold()
                    candidates = [c for c in candidates if normalized_quote in " ".join(unicodedata.normalize("NFKC", c.text).split()).casefold()]
                if not candidates:
                    continue
                label = note.source_labels[ref]
                source_query = tokenize(query + " " + label)
                source = max(candidates, key=lambda c: self.bm25.score(source_query, self._pos[c.chunk_id]))
                label_score = sum(self.bm25.idf[t] for t in set(q) & set(tokenize(label)))
                linked_sources.append((source, note, label_score, note_rank,
                                       self.bm25.score(q, self._pos[source.chunk_id])))
        ranked_sources = sorted(linked_sources, key=lambda item: (-item[2], item[3], -item[4]))
        sources = []
        source_ids = set()
        # Rank by relevance to the question; distinct passages in one long
        # page or section can support different claims.
        selected = []
        for candidate in ranked_sources:
            if len(selected) == SOURCE_SUPPORT_LIMIT:
                break
            source, _, _, _, _ = candidate
            if source.chunk_id not in source_ids:
                source_ids.add(source.chunk_id)
                selected.append(candidate)
        for source, _, _, _, _ in selected:
            sources.append({**source.row(0.0), "retrieval_role": "source_support",
                            "supports_notes": [n.chunk_id for s, n, _, _, _ in linked_sources if s.chunk_id == source.chunk_id]})
        rows = merge_sources(rows, sources)

        facts: list[dict] = []
        entities: list[dict] = []
        if use_graph and q_ents:
            ranked_facts = []
            seeds = q_ents[:6]
            seen: set[str] = set()
            for eid in seeds:
                ent = self.graph.entities.get(eid)
                if ent:
                    entities.append({"id": ent.id, "name": ent.name, "type": ent.type, "summary": ent.summary, "in_query": eid in q_ents})
                for r in self.graph.relations_of(eid):
                    if r.id in seen:
                        continue
                    seen.add(r.id)
                    # prefer facts touching a query entity
                    ranked_facts.append((0 if (r.src in q_ents or r.dst in q_ents) else 1, r.unresolved, -r.confidence, self.graph.fact_row(r)))
            ranked_facts.sort(key=lambda x: x[:3])
            facts = [{**f[3], "evidence": [cid for cid in f[3]["evidence"] if ok(self.by_id[cid])]} for f in ranked_facts]
        return {**bundle_facts(rows, facts, self.by_id), "entities": entities}

    def get(self, chunk_id: str) -> dict | None:
        c = self.by_id.get(chunk_id)
        return c.row() if c else None

    def stats(self) -> dict:
        docs = sorted({c.doc_id for c in self.chunks})
        return {"root": str(self.root), "n_docs": len(docs), "n_chunks": len(self.chunks),
                "n_papers": len({c.doc_id for c in self.chunks if c.doc_type == "paper"}), "docs": docs,
                "domains": sorted({c.domain for c in self.chunks}),
                "dense": self.embedder.name if self.embedder else None,
                "reranker": self.reranker.name if self.reranker else None, "graph": self.graph.stats()}


_INDEX: KnowledgeIndex | None = None


def index() -> KnowledgeIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = KnowledgeIndex()
    return _INDEX


def reload() -> KnowledgeIndex:
    global _INDEX
    _INDEX = KnowledgeIndex()
    return _INDEX


def search_knowledge(query: str, k: int = 5, domain: str | None = None, tasks: list[str] | None = None,
                     doc_type: str | None = None, expand_graph: bool = True,
                     session_id: str | None = None, scope: str = "all") -> dict:
    if not query or not query.strip():
        raise ValueError("query 为空")
    from .documents import search_documents

    project = index() if scope != "session" else None
    res = project.search(query, k=k, domain=domain, tasks=tasks, doc_type=doc_type, expand_graph=expand_graph) if project else {"chunks": [], "facts": [], "entities": []}
    uploaded = search_documents(query, session_id, k) if scope != "project" and doc_type in (None, "document") and domain in (None, "uploaded") else []
    # Rank fusion avoids comparing scores from two BM25 corpora directly.
    matches = [row for row in res["chunks"] if row["retrieval_role"] == "match"]
    combined = [(rank, priority, row) for priority, rows in enumerate((uploaded, matches)) for rank, row in enumerate(rows)]
    rows = [{**row, "retrieval_role": "match"} for _, _, row in sorted(combined, key=lambda item: item[:2])[:k]]
    rows = merge_sources(rows, [row for row in res["chunks"] if row["retrieval_role"] == "source_support"])
    res.update(bundle_facts(rows, res["facts"], project.by_id) if project else {"chunks": rows})
    note = ("按原文支持的结论引用 [chunk_id]；source_support 是关联原文，关系引用 evidence 块。"
            "本次记录的读数使用分析工具结果。"
            if res["chunks"] or res["facts"] else "本次检索无匹配片段。")
    return {"status": "ok", "query": query, **res, "evidence_scope": "reference_material", "note": note}
