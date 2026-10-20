"""CLI: python -m eegagent.knowledge <command>

  stats                                   index & graph statistics
  search "query" [-k 5] [--no-graph] [--domain d] [--doc-type note|paper]
  graph <entity> [--rel r] [--depth 1]    neighbors with evidence
  graph --path <a> <b>                    shortest relation path
  build                                   warm the vector cache
  ingest <pdf|txt> [--doc-id id] [--title t] [--year y] [--authors a] [--tasks a,b]
  extract <doc_id>                        LLM entity/relation proposals → graph/extracted/<doc_id>.jsonl
  review <doc_id> [--accept-all | --accept i,j | --reject i,j]
  contextualize <doc_id>                  LLM chunk context sidecar
  eval <qa.yaml> [-k 5]                   document hit rate, MRR, reviewed original-excerpt coverage
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eegagent.knowledge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--no-graph", action="store_true")
    s.add_argument("--domain")
    s.add_argument("--doc-type")
    g = sub.add_parser("graph")
    g.add_argument("entity", nargs="?")
    g.add_argument("--rel")
    g.add_argument("--depth", type=int, default=1)
    g.add_argument("--path", nargs=2, metavar=("A", "B"))
    sub.add_parser("build")
    i = sub.add_parser("ingest")
    i.add_argument("files", nargs="+")
    i.add_argument("--doc-id")
    i.add_argument("--title")
    i.add_argument("--year")
    i.add_argument("--authors")
    i.add_argument("--venue")
    i.add_argument("--doi")
    i.add_argument("--url")
    i.add_argument("--license")
    i.add_argument("--doc-type", choices=["paper", "documentation"], default="paper")
    i.add_argument("--content-selector", default="main")
    i.add_argument("--tasks", default="")
    i.add_argument("--domain", default="paper")
    e = sub.add_parser("extract")
    e.add_argument("doc_ids", nargs="+")
    r = sub.add_parser("review")
    r.add_argument("doc_id")
    r.add_argument("--accept-all", action="store_true")
    r.add_argument("--accept", default="")
    r.add_argument("--reject", default="")
    c = sub.add_parser("contextualize")
    c.add_argument("doc_ids", nargs="+")
    v = sub.add_parser("eval")
    v.add_argument("qa")
    v.add_argument("-k", type=int, default=5)
    a = ap.parse_args(argv)

    from .index import KnowledgeIndex

    if a.cmd == "ingest":
        from .ingest import ingest_file

        for f in a.files:
            out = ingest_file(Path(f), doc_id=a.doc_id if len(a.files) == 1 else None, title=a.title if len(a.files) == 1 else None,
                              year=a.year, authors=a.authors, venue=a.venue, doi=a.doi, url=a.url, license=a.license,
                              doc_type=a.doc_type, content_selector=a.content_selector, domain=a.domain,
                              tasks=[t.strip() for t in a.tasks.split(",") if t.strip()])
            print("wrote", out)
        return 0

    idx = KnowledgeIndex(embedder=None if a.cmd in ("stats", "graph", "review") else "auto")
    if a.cmd == "stats":
        print(json.dumps(idx.stats(), ensure_ascii=False, indent=1))
        for w in idx.graph.warnings[:50]:
            print("warning:", w)
        return 0
    if a.cmd == "search":
        res = idx.search(a.query, k=a.k, domain=a.domain, doc_type=a.doc_type, expand_graph=not a.no_graph)
        for ch in res["chunks"]:
            print(f"[{ch['chunk_id']}] {ch['score']:.4f} {ch['doc_id']} › {ch['section']}\n  {ch['text'][:200].replace(chr(10), ' ')}")
        for f in res["facts"]:
            print(f"  fact {f['text']}  evidence={f['evidence']}")
        return 0
    if a.cmd == "graph":
        gr = idx.graph
        if a.path:
            chain = gr.path(a.path[0], a.path[1])
            for rel in chain:
                print(gr.fact_row(rel)["text"], rel.chunk_ids)
            if not chain:
                print("no path")
            return 0
        ent = gr.entity(a.entity or "")
        if not ent:
            print("unknown entity")
            return 1
        print(json.dumps(ent.row(), ensure_ascii=False, indent=1))
        for rel in gr.neighbors(ent.id, a.rel, a.depth):
            print(" ", gr.fact_row(rel)["text"], rel.chunk_ids)
        return 0
    if a.cmd == "build":
        print(json.dumps(idx.stats(), ensure_ascii=False))
        return 0
    if a.cmd == "extract":
        from .ingest import LLMClient, extract_graph

        llm = LLMClient()
        for d in a.doc_ids:
            rows = extract_graph(d, llm, idx)
            print(d, "proposed", len(rows), "→ review with: python -m eegagent.knowledge review", d, "--accept-all")
        return 0
    if a.cmd == "review":
        from .ingest import review

        print(review(a.doc_id, accept=[x for x in a.accept.split(",") if x], reject=[x for x in a.reject.split(",") if x], accept_all=a.accept_all))
        return 0
    if a.cmd == "contextualize":
        from .ingest import LLMClient, contextualize

        llm = LLMClient()
        for d in a.doc_ids:
            print("wrote", contextualize(d, llm, idx))
        return 0
    if a.cmd == "eval":
        from .evaluate import evaluate

        print(json.dumps(evaluate(idx, Path(a.qa), k=a.k), ensure_ascii=False, indent=1))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
