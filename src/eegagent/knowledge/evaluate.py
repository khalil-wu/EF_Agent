"""Evaluate ranked document hits separately from delivery of reviewed original excerpts."""

from __future__ import annotations

from pathlib import Path


def evaluate(index, qa_path: Path, k: int = 5) -> dict:
    import yaml

    items = yaml.safe_load(qa_path.read_text(encoding="utf-8")) or []
    n = 0
    hits = {1: 0, 3: 0, k: 0}
    rr = 0.0
    ent_total = ent_hit = 0
    failures: list[dict] = []
    evidence_results: list[dict] = []
    for it in items:
        q = it["q"]
        want_docs = set(it.get("docs") or [])
        want_ents = set(it.get("entities") or [])
        res = index.search(q, k=k)
        docs = [c["doc_id"] for c in res["chunks"] if c["retrieval_role"] == "match"]
        n += 1
        rank = next((i + 1 for i, d in enumerate(docs) if d in want_docs), None) if want_docs else None
        if want_docs:
            if rank:
                rr += 1.0 / rank
                for kk in hits:
                    if rank <= kk:
                        hits[kk] += 1
            else:
                failures.append({"q": q, "want": sorted(want_docs), "got": docs[:k]})
        if want_ents:
            ent_total += 1
            got = {e["id"] for e in res["entities"]} | {f["src"]["id"] for f in res["facts"]} | {f["dst"]["id"] for f in res["facts"]}
            if want_ents & got:
                ent_hit += 1
            else:
                failures.append({"q": q, "want_entities": sorted(want_ents), "got_entities": sorted(got)[:10]})
        if it.get("evidence"):
            checks = []
            for expected in it["evidence"]:
                candidates = [c for c in res["chunks"] if c["doc_id"] == expected["doc_id"] and
                              c["source_kind"] in ("paper_excerpt", "official_documentation") and
                              ("page" not in expected or c.get("page") == expected["page"]) and
                              ("section" not in expected or c["section"] == expected["section"])]
                matching = []
                for c in candidates:
                    text = " ".join(c["text"].split()).casefold()
                    if all(" ".join(phrase.split()).casefold() in text for phrase in expected.get("contains", [])):
                        matching.append(c["chunk_id"])
                checks.append({"expected": expected, "passed": bool(matching), "chunks": matching})
            evidence_results.append({"q": q, "passed": all(c["passed"] for c in checks), "checks": checks})
            if not evidence_results[-1]["passed"]:
                failures.append({"q": q, "missing_evidence": [c["expected"] for c in checks if not c["passed"]]})
    n_docs = sum(1 for it in items if it.get("docs"))
    return {"n": n, "doc_evaluated_n": n_docs, "doc_hit@1": round(hits[1] / max(n_docs, 1), 3),
            "doc_hit@3": round(hits[3] / max(n_docs, 1), 3), f"doc_hit@{k}": round(hits[k] / max(n_docs, 1), 3),
            "mrr": round(rr / max(n_docs, 1), 3), "entity_hit_rate": round(ent_hit / max(ent_total, 1), 3) if ent_total else None,
            "evidence_evaluated_n": len(evidence_results),
            "evidence_hit_rate": round(sum(c["passed"] for c in evidence_results) / len(evidence_results), 3) if evidence_results else None,
            "evidence_results": evidence_results, "failures": failures}
