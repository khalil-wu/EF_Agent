"""Knowledge graph: typed entities and evidence-backed relations over the chunk store.

Sources
  knowledge/graph/*.yaml                 curated (status accepted, source curated)
  knowledge/graph/extracted/*.jsonl      LLM proposals (status proposed until reviewed)

Every relation carries evidence refs "doc_id#section" that are resolved to chunk ids at load time; relations
whose evidence cannot be resolved are kept but flagged (unresolved) and rank lower.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .tokenize import normalize

ENTITY_TYPES = {"paper", "dataset", "device", "method", "model", "band_scheme", "artifact", "construct", "software",
                "task", "protocol", "metric", "term"}
RELATIONS = {"proposes", "evaluated_on", "recorded_with", "requires_input", "uses_band_scheme", "implemented_in",
             "variant_of", "affected_by", "indicates", "mitigates", "defines", "part_of", "trained_on",
             "compatible_with", "contradicts", "reports", "applies_to", "derived_from", "cites"}
REL_ZH = {"proposes": "提出", "evaluated_on": "在…上评测", "recorded_with": "用…采集", "requires_input": "需要输入",
          "uses_band_scheme": "使用频带方案", "implemented_in": "实现于", "variant_of": "是…的变体", "affected_by": "受…影响",
          "indicates": "指示", "mitigates": "缓解", "defines": "定义", "part_of": "属于", "trained_on": "训练于",
          "compatible_with": "兼容", "contradicts": "与…矛盾", "reports": "报告", "applies_to": "适用于",
          "derived_from": "源自", "cites": "引用"}


@dataclass
class Entity:
    id: str
    type: str
    name: str
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    attrs: dict = field(default_factory=dict)
    refs: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    source: str = "curated"
    status: str = "accepted"

    def row(self) -> dict:
        return {"id": self.id, "type": self.type, "name": self.name, "aliases": self.aliases, "summary": self.summary,
                "attrs": self.attrs, "evidence": self.chunk_ids, "source": self.source, "status": self.status}


@dataclass
class Relation:
    id: str
    src: str
    rel: str
    dst: str
    evidence: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    note: str = ""
    attrs: dict = field(default_factory=dict)
    source: str = "curated"
    status: str = "accepted"
    unresolved: bool = False
    evidence_quote: str = ""


class KnowledgeGraph:
    def __init__(self, root: Path, chunks: list, include_proposed: bool | None = None):
        self.root = root
        self.entities: dict[str, Entity] = {}
        self.relations: dict[str, Relation] = {}
        self.warnings: list[str] = []
        self._out: dict[str, list[str]] = defaultdict(list)
        self._in: dict[str, list[str]] = defaultdict(list)
        self._alias: list[tuple[str, str]] = []  # (normalized alias, entity id)
        self._chunk_index = self._build_chunk_index(chunks)
        self._chunks_by_id = {c.chunk_id: c for c in chunks}
        if include_proposed is None:
            include_proposed = os.environ.get("EEGAGENT_GRAPH_INCLUDE_PROPOSED", "") == "1"
        self.include_proposed = include_proposed
        self._load(root)
        self._finalize()

    # ------------------------------------------------------------ loading

    @staticmethod
    def _build_chunk_index(chunks: list) -> dict[str, list]:
        by_doc: dict[str, list] = defaultdict(list)
        for c in chunks:
            by_doc[c.doc_id].append(c)
        return by_doc

    @staticmethod
    def _norm_section(s: str) -> str:
        return re.sub(r"[\s\W_]+", "", s.lower())

    def resolve_ref(self, ref: str) -> list[str]:
        """'doc_id#section' -> chunk ids (exact, then prefix, then containment). 'doc_id' alone -> first chunk."""
        if not ref:
            return []
        doc, _, sec = ref.partition("#")
        chunks = self._chunk_index.get(doc.strip(), [])
        if not chunks:
            return []
        if not sec:
            return [chunks[0].chunk_id]
        want = self._norm_section(sec)
        exact = [c.chunk_id for c in chunks if self._norm_section(c.section) == want]
        if exact:
            return exact
        pref = [c.chunk_id for c in chunks if self._norm_section(c.section).startswith(want) or want.startswith(self._norm_section(c.section))]
        if pref:
            return pref
        cont = [c.chunk_id for c in chunks if want in self._norm_section(c.section) or self._norm_section(c.section) in want]
        return cont

    def _add_entity(self, d: dict, source: str, status: str) -> None:
        eid = str(d.get("id") or "").strip()
        if not eid:
            self.warnings.append(f"entity without id: {d}")
            return
        etype = str(d.get("type") or "term")
        if etype not in ENTITY_TYPES:
            self.warnings.append(f"{eid}: unknown entity type {etype}")
        e = Entity(eid, etype, str(d.get("name") or eid), [str(a) for a in (d.get("aliases") or [])],
                   str(d.get("summary") or ""), dict(d.get("attrs") or {}), [str(r) for r in (d.get("refs") or [])],
                   source=source, status=status)
        if eid in self.entities:  # merge aliases/refs
            cur = self.entities[eid]
            cur.aliases = sorted(set(cur.aliases) | set(e.aliases))
            cur.refs = sorted(set(cur.refs) | set(e.refs))
            if not cur.summary:
                cur.summary = e.summary
            cur.attrs.update({k: v for k, v in e.attrs.items() if k not in cur.attrs})
            return
        self.entities[eid] = e

    def _add_relation(self, d: dict, source: str, status: str) -> None:
        src, rel, dst = str(d.get("src", "")).strip(), str(d.get("rel", "")).strip(), str(d.get("dst", "")).strip()
        if not (src and rel and dst):
            self.warnings.append(f"relation missing src/rel/dst: {d}")
            return
        if rel not in RELATIONS:
            self.warnings.append(f"{src} -{rel}-> {dst}: unknown relation")
        rid = str(d.get("id") or ("r_" + hashlib.sha1(f"{src}|{rel}|{dst}".encode("utf-8")).hexdigest()[:8]))
        r = Relation(rid, src, rel, dst, [str(x) for x in (d.get("evidence") or [])], confidence=float(d.get("confidence", 1.0)),
                     note=str(d.get("note") or ""), attrs=dict(d.get("attrs") or {}), source=source, status=str(d.get("status") or status),
                     evidence_quote=str(d.get("evidence_quote") or "").strip())
        if rid in self.relations:
            cur = self.relations[rid]
            cur.evidence = sorted(set(cur.evidence) | set(r.evidence))
            if not cur.note:
                cur.note = r.note
            return
        self.relations[rid] = r

    def _load(self, root: Path) -> None:
        gdir = root / "graph"
        if not gdir.exists():
            return
        try:
            import yaml
        except ImportError:  # pragma: no cover
            yaml = None
        for p in sorted(gdir.glob("*.yaml")) + sorted(gdir.glob("*.yml")):
            if yaml is None:
                self.warnings.append("pyyaml not installed; curated graph skipped")
                break
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for e in data.get("entities", []) or []:
                self._add_entity(e, "curated", "accepted")
            for r in data.get("relations", []) or []:
                self._add_relation(r, "curated", "accepted")
        for p in sorted((gdir / "extracted").glob("*.jsonl")) if (gdir / "extracted").exists() else []:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    self.warnings.append(f"{p.name}: bad json line")
                    continue
                status = str(d.get("status") or "proposed")
                if status == "rejected":
                    continue
                if d.get("kind") == "relation" and d.get("quote_located") is False:
                    status = "proposed"
                    d = dict(d, status=status, evidence=[])
                if status == "proposed" and not self.include_proposed:
                    continue
                kind = d.get("kind")
                if kind == "entity":
                    self._add_entity(d, "extracted", status)
                elif kind == "relation":
                    if status == "proposed":
                        d = dict(d, confidence=min(float(d.get("confidence", 0.6)), 0.6))
                    self._add_relation(d, "extracted", status)

    def _finalize(self) -> None:
        for e in self.entities.values():
            ids: list[str] = []
            for ref in e.refs:
                ids.extend(self.resolve_ref(ref))
            e.chunk_ids = sorted(set(ids))
            for a in [e.name, *e.aliases]:
                na = normalize(a).strip()
                if len(na) >= 2:
                    self._alias.append((na, e.id))
        # longer aliases first so "LibEER 五频带 DE" wins over "DE"
        self._alias.sort(key=lambda x: -len(x[0]))
        self._alias_patterns = {alias: re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")
                                for alias, _ in self._alias if alias.isascii()}
        for r in list(self.relations.values()):
            for end in (r.src, r.dst):
                if end not in self.entities:
                    self.warnings.append(f"relation {r.id}: unknown entity {end}")
            ids = []
            for ref in r.evidence:
                ids.extend(self.resolve_ref(ref))
            if r.evidence_quote:
                quote = re.sub(r"\s+", " ", r.evidence_quote).lower()
                ids = [cid for cid in ids if quote in re.sub(r"\s+", " ", self._chunks_by_id[cid].text).lower()]
            r.chunk_ids = sorted(set(ids))
            r.unresolved = not r.chunk_ids
            if r.unresolved:
                self.warnings.append(f"relation {r.id} ({r.src} -{r.rel}-> {r.dst}): evidence not resolved {r.evidence}")
            self._out[r.src].append(r.id)
            self._in[r.dst].append(r.id)

    # ------------------------------------------------------------ linking

    def link_text(self, text: str, limit: int = 8) -> list[str]:
        """Entity ids whose name/alias occurs in text. ASCII aliases need word boundaries; CJK use substring."""
        t = normalize(text)
        found: list[str] = []
        for alias, eid in self._alias:
            if eid in found:
                continue
            if alias.isascii():
                if len(alias) < 3 and alias not in ("de", "faa", "psd", "lds", "ica", "asr", "car"):
                    continue
                if alias in t and self._alias_patterns[alias].search(t):
                    found.append(eid)
            elif alias in t:
                found.append(eid)
            if len(found) >= limit:
                break
        return found

    def link_chunks(self, chunks: list) -> None:
        for c in chunks:
            c.entities = self.link_text(c.index_text, limit=12)

    # ------------------------------------------------------------ queries

    def entity(self, key: str) -> Entity | None:
        if key in self.entities:
            return self.entities[key]
        nk = normalize(key).strip()
        for alias, eid in self._alias:
            if alias == nk:
                return self.entities[eid]
        hits = self.link_text(key, limit=1)
        return self.entities[hits[0]] if hits else None

    def relations_of(self, eid: str, rel: str | None = None, direction: str = "both") -> list[Relation]:
        ids: list[str] = []
        if direction in ("both", "out"):
            ids.extend(self._out.get(eid, []))
        if direction in ("both", "in"):
            ids.extend(self._in.get(eid, []))
        out = [self.relations[i] for i in dict.fromkeys(ids)]
        if rel:
            out = [r for r in out if r.rel == rel]
        out.sort(key=lambda r: (r.unresolved, -r.confidence))
        return out

    def neighbors(self, eid: str, rel: str | None = None, depth: int = 1, limit: int = 40) -> list[Relation]:
        seen: set[str] = set()
        frontier = [eid]
        out: list[Relation] = []
        for _ in range(max(1, depth)):
            nxt: list[str] = []
            for e in frontier:
                for r in self.relations_of(e, rel):
                    if r.id in seen:
                        continue
                    seen.add(r.id)
                    out.append(r)
                    other = r.dst if r.src == e else r.src
                    nxt.append(other)
                    if len(out) >= limit:
                        return out
            frontier = nxt
        return out

    def path(self, a: str, b: str, max_hops: int = 3) -> list[Relation]:
        if a == b or a not in self.entities or b not in self.entities:
            return []
        prev: dict[str, tuple[str, str] | None] = {a: None}
        q = deque([(a, 0)])
        while q:
            cur, d = q.popleft()
            if d >= max_hops:
                continue
            for r in self.relations_of(cur):
                other = r.dst if r.src == cur else r.src
                if other in prev:
                    continue
                prev[other] = (cur, r.id)
                if other == b:
                    chain: list[Relation] = []
                    node = b
                    while prev[node] is not None:
                        p, rid = prev[node]  # type: ignore[misc]
                        chain.append(self.relations[rid])
                        node = p
                    chain.reverse()
                    return chain
                q.append((other, d + 1))
        return []

    def fact_row(self, r: Relation) -> dict:
        s, d = self.entities.get(r.src), self.entities.get(r.dst)
        sn = s.name if s else r.src
        dn = d.name if d else r.dst
        text = f"{sn} —{r.rel}（{REL_ZH.get(r.rel, r.rel)}）→ {dn}"
        if r.note:
            text += f"：{r.note}"
        return {"fact_id": r.id, "src": {"id": r.src, "name": sn, "type": s.type if s else ""},
                "rel": r.rel, "dst": {"id": r.dst, "name": dn, "type": d.type if d else ""},
                "note": r.note, "confidence": r.confidence, "source": r.source, "status": r.status,
                "evidence": r.chunk_ids, "unresolved": r.unresolved, "text": text,
                "evidence_role": "reference_relation", "evidence_quote": r.evidence_quote}

    def stats(self) -> dict:
        types: dict[str, int] = defaultdict(int)
        for e in self.entities.values():
            types[e.type] += 1
        rels: dict[str, int] = defaultdict(int)
        for r in self.relations.values():
            rels[r.rel] += 1
        return {"n_entities": len(self.entities), "n_relations": len(self.relations), "entity_types": dict(types),
                "relation_types": dict(rels), "unresolved_relations": sum(1 for r in self.relations.values() if r.unresolved),
                "n_warnings": len(self.warnings), "include_proposed": self.include_proposed}
