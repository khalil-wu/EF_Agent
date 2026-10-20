"""Backward-compatible shim: the knowledge layer now lives in eegagent.knowledge."""

from eegagent.knowledge import KnowledgeIndex, index, knowledge_dir, reload, search_knowledge, tokenize
from eegagent.knowledge.graph import KnowledgeGraph
from eegagent.knowledge.index import bundle_facts

__all__ = ["KnowledgeGraph", "KnowledgeIndex", "index", "knowledge_dir", "reload", "search_knowledge", "tokenize"]


def knowledge_graph(entity: str | None = None, relation: str | None = None, depth: int = 1,
                    path_from: str | None = None, path_to: str | None = None, list_types: bool = False) -> dict:
    """Tool: look up relations together with their readable, cited support chunks."""
    idx = index()
    g = idx.graph
    if list_types:
        return {"status": "ok", "stats": g.stats(), "entities": [e.row() | {"n_relations": len(g.relations_of(e.id))} for e in g.entities.values()][:200]}
    if path_from and path_to:
        a, b = g.entity(path_from), g.entity(path_to)
        if not a or not b:
            return {"status": "ok", "path": [], "note": f"实体未找到：{'' if a else path_from} {'' if b else path_to}".strip()}
        chain = g.path(a.id, b.id)
        bundle = bundle_facts([], [g.fact_row(r) for r in chain], idx.by_id, support_limit=3, fact_limit=3)
        if len(bundle["facts"]) != len(chain):
            return {"status": "ok", "from": a.row(), "to": b.row(), "path": [], "chunks": [],
                    "note": "路径中的关系缺少可读取的依据；不能作为两者联系的证据"}
        return {"status": "ok", "from": a.row(), "to": b.row(), "path": bundle["facts"], "chunks": bundle["chunks"],
                "note": "" if chain else "3 跳内没有路径；说明知识库未记录两者的联系"}
    if not entity:
        raise ValueError("需要 entity，或 path_from + path_to，或 list_types=true")
    e = g.entity(entity)
    if not e:
        return {"status": "ok", "entity": None, "facts": [], "note": f"知识库没有实体“{entity}”；不要编造它的属性"}
    facts = [g.fact_row(r) for r in g.neighbors(e.id, relation, max(1, min(int(depth), 2)))]
    return {"status": "ok", "entity": e.row(), **bundle_facts([], facts, idx.by_id),
            "note": "引用用 evidence 里的块 id；proposed 或 unresolved 关系只是线索。accepted 是图谱审核状态，confidence 是条目分数，不是当前 EEG 的来源/状态证明"}
