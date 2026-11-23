"""KnowledgeIndex: chunks (notes + papers) + BM25 + optional dense vectors + graph; hybrid retrieval with
RRF fusion of three retrievers (BM25, dense, entity-linked defining chunks), optional cross-encoder rerank, and
1-hop graph expansion (LightRAG-style dual level: text chunks + relation facts).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
import unicodedata

import numpy as np

from .bm25 import BM25
from .chunking import Chunk, knowledge_dir, load_all_chunks
from .embed import APIReranker, BaseEmbedder, Reranker, VectorCache, embed_chunks, embed_missing, make_embedder, make_reranker
from .graph import KnowledgeGraph
from .tokenize import tokenize
from .revision import revision

RRF_K = 60
CAND = 50
RERANK_K = 10
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
                 reranker: Reranker | APIReranker | None | str = "auto", use_cache: bool = True,
                 include_proposed: bool | None = None, background_embed: bool = False):
        self.root = root or knowledge_dir()
        self.revision = revision(self.root)
        self.chunks: list[Chunk] = load_all_chunks(self.root)
        self.by_id = {c.chunk_id: c for c in self.chunks}
        self._pos = {c.chunk_id: i for i, c in enumerate(self.chunks)}
        self.graph = KnowledgeGraph(self.root, self.chunks, include_proposed)
        self.graph.link_chunks(self.chunks)
        self.bm25 = BM25([c.tokens for c in self.chunks])
        self.embedder = make_embedder() if embedder == "auto" else embedder
        self.reranker = make_reranker() if reranker == "auto" else reranker
        self.rerank_mode = os.environ.get("EEGAGENT_RERANK_MODE", "blend")
        self.vectors = None
        self.dense_state = {"embedded": 0, "total": len(self.chunks), "ready": False, "error": None}
        self._dense_down_until = 0.0
        self._rerank_down_until = 0.0
        if self.embedder and self.chunks:
            cache = VectorCache(self.embedder.name) if use_cache else None
            if cache:
                self.dense_state["embedded"] = sum(cache.get(c.chunk_id, c.text_sha) is not None for c in self.chunks)
            if cache and self.dense_state["embedded"] == self.dense_state["total"]:
                self.vectors = self._matrix(cache)
                self.dense_state["ready"] = True
            elif not background_embed:
                self.vectors = embed_chunks(self.embedder, self.chunks, use_cache)
                self.dense_state["embedded"] = self.dense_state["total"]
                self.dense_state["ready"] = True
            else:
                threading.Thread(target=self._build_dense, args=(cache,), daemon=True).start()

    def _matrix(self, cache: VectorCache) -> np.ndarray:
        missing = [c.chunk_id for c in self.chunks if cache.get(c.chunk_id, c.text_sha) is None]
        if missing:
            raise ValueError(f"vector cache is missing {len(missing)} chunks")
        return np.stack([cache.get(c.chunk_id, c.text_sha) for c in self.chunks])

    def _build_dense(self, cache: VectorCache | None) -> None:
        """Fill the vector matrix on a daemon thread; search stays sparse+graph meanwhile."""
        try:
            if cache is not None:
                embed_missing(self.embedder, self.chunks, cache,
                              on_progress=lambda done, total: self.dense_state.update(embedded=done))
                self.vectors = self._matrix(cache)
            else:
                self.vectors = np.asarray(self.embedder.encode([c.index_text for c in self.chunks]), dtype=np.float32)
            self.dense_state["embedded"] = self.dense_state["total"]
            self.dense_state["ready"] = True
        except Exception as exc:  # noqa: BLE001
            self.dense_state["error"] = str(exc)[:300]

    # ------------------------------------------------------------ retrieval

    def _dense_top(self, query: str, n: int) -> list[tuple[int, float]]:
        if self.vectors is None or self.embedder is None or time.monotonic() < self._dense_down_until:
            return []
        try:
            qv = np.asarray(self.embedder.encode_query(query), dtype=np.float32)
            if qv.ndim != 1 or qv.shape[0] != self.vectors.shape[1] or not np.isfinite(qv).all():
                raise ValueError("query embedding has invalid shape or values")
            sims = self.vectors @ qv
            order = np.argsort(-sims)[:n]
            return [(int(i), float(sims[i])) for i in order if sims[i] > 0]
        except Exception as exc:  # noqa: BLE001
            self._dense_down_until = time.monotonic() + 60
            print(f"[knowledge] dense retrieval unavailable: {exc}")
            return []

    def search(self, query: str, k: int = 5, domain: str | None = None, tasks: list[str] | None = None,
               doc_type: str | None = None, expand_graph: bool = True) -> dict:
        if not self.chunks:
            return {"chunks": [], "facts": [], "entities": []}
        q = tokenize(query)
        use_graph = expand_graph and bool(self.graph.entities)
        q_ents = self.graph.link_text(query, limit=6) if use_graph else []

        # Cross-lingual entity-alias expansion for sparse BM25 retrieval.
        if q_ents:
            q_weights: dict[str, float] = {}
            for tok in q:
                q_weights[tok] = 1.0
            for eid in q_ents:
                ent_obj = self.graph.entities.get(eid)
                if not ent_obj:
                    continue
                for alias in [ent_obj.name, *ent_obj.aliases[:6]]:
                    for tok in tokenize(alias):
                        if tok not in q_weights:
                            q_weights[tok] = 0.35
            bm = self.bm25.top_weighted(q_weights, CAND)
        else:
            bm = self.bm25.top(q, CAND)

        de = self._dense_top(query, CAND)

        # Third retriever (entity & 2-hop graph propagation level, inspired by HippoRAG 2).
        ent: list[int] = []
        ent_scores: dict[str, float] = {eid: 1.0 for eid in q_ents}
        if use_graph and q_ents:
            frontier = list(q_ents)
            for hop_decay in (0.5, 0.22):
                next_frontier: list[str] = []
                for eid in frontier:
                    base_score = ent_scores.get(eid, 0.0)
                    for r in self.graph.relations_of(eid):
                        if r.unresolved:
                            continue
                        other = r.dst if r.src == eid else r.src
                        propagated = base_score * hop_decay * max(0.1, min(1.0, r.confidence))
                        if propagated > ent_scores.get(other, 0.0):
                            if other not in ent_scores:
                                next_frontier.append(other)
                            ent_scores[other] = propagated
                frontier = next_frontier[:18]

            graph_chunk_scores: dict[int, float] = {}
            for eid, e_score in ent_scores.items():
                ent_obj = self.graph.entities.get(eid)
                if ent_obj:
                    for cid in ent_obj.chunk_ids[:3]:
                        pos = self._pos.get(cid)
                        if pos is not None:
                            graph_chunk_scores[pos] = graph_chunk_scores.get(pos, 0.0) + e_score
                for r in self.graph.relations_of(eid):
                    if r.unresolved or not r.chunk_ids:
                        continue
                    rel_weight = 0.6 * max(0.1, min(1.0, r.confidence)) * max(ent_scores.get(r.src, 0.0), ent_scores.get(r.dst, 0.0))
                    for cid in r.chunk_ids[:2]:
                        pos = self._pos.get(cid)
                        if pos is not None:
                            graph_chunk_scores[pos] = graph_chunk_scores.get(pos, 0.0) + rel_weight
            ent = [pos for pos, _ in sorted(graph_chunk_scores.items(), key=lambda x: -x[1])[:CAND]]

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
        if self.reranker and cands and time.monotonic() >= self._rerank_down_until:
            try:
                rs = self.reranker.scores(query, [self.chunks[i].index_text for i in cands])
                rs = np.asarray(rs, dtype=np.float64)
                if rs.shape != (len(cands),) or not np.isfinite(rs).all():
                    raise ValueError("rerank scores have invalid shape or values")
                rs = rs.tolist()
            except Exception as exc:  # noqa: BLE001
                self._rerank_down_until = time.monotonic() + 60
                print(f"[knowledge] reranker unavailable: {exc}")
                rs = None
            if rs is not None:
                if self.rerank_mode == "replace":
                    cands = [i for _, i in sorted(zip(rs, cands), key=lambda x: -x[0])]
                    scores = {i: float(s) for s, i in zip(sorted(rs, reverse=True), cands)}
                else:
                    rr_rank = [0] * len(cands)
                    for rank, j in enumerate(sorted(range(len(cands)), key=lambda j: (-rs[j], j))):
                        rr_rank[j] = rank
                    pairs = sorted(zip(cands, [1.0 / (RERANK_K + j + 1) + 1.0 / (RERANK_K + rr_rank[j] + 1)
                                               for j in range(len(cands))]), key=lambda p: -p[1])
                    cands = [i for i, _ in pairs]
                    scores = {i: b for i, b in pairs}
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
            seeds = sorted(ent_scores, key=lambda eid: -ent_scores[eid])[:12]
            seen: set[str] = set()
            q_set = set(q_ents)
            for eid in seeds:
                ent_obj = self.graph.entities.get(eid)
                if ent_obj:
                    entities.append({"id": ent_obj.id, "name": ent_obj.name, "type": ent_obj.type,
                                     "summary": ent_obj.summary, "in_query": eid in q_set})
                for r in self.graph.relations_of(eid):
                    if r.id in seen:
                        continue
                    seen.add(r.id)
                    both_activated = ent_scores.get(r.src, 0.0) > 0 and ent_scores.get(r.dst, 0.0) > 0
                    touches_query = r.src in q_set or r.dst in q_set
                    priority = 0 if (both_activated and touches_query) else (1 if both_activated else (2 if touches_query else 3))
                    rel_score = r.confidence * (ent_scores.get(r.src, 0.0) + ent_scores.get(r.dst, 0.0))
                    ranked_facts.append((priority, r.unresolved, -rel_score, self.graph.fact_row(r)))
            ranked_facts.sort(key=lambda x: x[:3])
            facts = [{**f[3], "evidence": [cid for cid in f[3]["evidence"] if ok(self.by_id[cid])]} for f in ranked_facts]
        return {**bundle_facts(rows, facts, self.by_id), "entities": entities}

    def get(self, chunk_id: str) -> dict | None:
        c = self.by_id.get(chunk_id)
        return c.row() if c else None

    def stats(self) -> dict:
        docs = sorted({c.doc_id for c in self.chunks})
        return {"root": str(self.root), "revision": self.revision, "n_docs": len(docs), "n_chunks": len(self.chunks),
                "n_papers": len({c.doc_id for c in self.chunks if c.doc_type == "paper"}), "docs": docs,
                "domains": sorted({c.domain for c in self.chunks}),
                "dense": self.embedder.name if self.embedder else None,
                "dense_ready": self.dense_state["ready"],
                "dense_progress": {"embedded": self.dense_state["embedded"], "total": self.dense_state["total"],
                                   "error": self.dense_state["error"]},
                "reranker": self.reranker.name if self.reranker else None,
                **({"rerank_mode": self.rerank_mode} if self.reranker else {}), "graph": self.graph.stats()}


_INDEX: KnowledgeIndex | None = None


def index() -> KnowledgeIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = KnowledgeIndex(background_embed=True)
    elif _INDEX.revision != revision(_INDEX.root):
        _INDEX = KnowledgeIndex(_INDEX.root, background_embed=True)
    return _INDEX


def reload() -> KnowledgeIndex:
    global _INDEX
    _INDEX = KnowledgeIndex(background_embed=True)
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
    # Balanced RRF fusion across session documents and project knowledge instead of blindly burying project papers.
    matches = [row for row in res["chunks"] if row["retrieval_role"] == "match"]
    scored_pool: list[tuple[float, int, dict]] = []
    for rank, row in enumerate(uploaded):
        scored_pool.append((1.02 / (20 + rank + 1), 0, row))
    for rank, row in enumerate(matches):
        scored_pool.append((1.0 / (20 + rank + 1), 1, row))
    scored_pool.sort(key=lambda item: (-item[0], item[1]))
    rows = [{**row, "retrieval_role": "match"} for _, _, row in scored_pool[:k]]
    rows = merge_sources(rows, [row for row in res["chunks"] if row["retrieval_role"] == "source_support"])
    res.update(bundle_facts(rows, res["facts"], project.by_id) if project else {"chunks": rows})
    note = ("按原文支持的结论引用 [chunk_id]；source_support 是关联原文，关系引用 evidence 块。"
            "本次记录的读数使用分析工具结果。"
            if res["chunks"] or res["facts"] else "本次检索无匹配片段。")
    return {"status": "ok", "query": query, **res, "index_revision": project.revision if project else None,
            "evidence_scope": "reference_material", "note": note}
