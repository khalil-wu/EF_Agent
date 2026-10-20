from pathlib import Path

from eegagent.knowledge import KnowledgeIndex, knowledge_dir, tokenize
from eegagent.knowledge.graph import ENTITY_TYPES, RELATIONS, KnowledgeGraph


def _index() -> KnowledgeIndex:
    return KnowledgeIndex(knowledge_dir(), embedder=None, reranker=None)


def test_tokenizer_mixes_ascii_and_cjk():
    toks = tokenize("差分熵 DE 0.5–4 Hz LibEER filtfilt")
    assert "de" in toks and "libeer" in toks and "filtfilt" in toks and "0.5-4" in toks or "0.5" in toks
    assert "差分" in toks and "分熵" in toks and "熵" in toks
    assert 'labels_ext_anotation' in tokenize('labels\\_ext\\_anotation')


def test_repo_notes_index_answers_domain_queries():
    idx = _index()
    st = idx.stats()
    assert st["n_docs"] >= 13 and st["n_chunks"] >= 40
    top = idx.search("差分熵 LibEER 频带 filtfilt CAMER", k=3)
    matches = [row for row in top["chunks"] if row["retrieval_role"] == "match"]
    assert matches and matches[0]["doc_id"] == "methods_de"
    assert matches[0]["chunk_id"].startswith("k_") and "score" in matches[0]
    deap = idx.search("DEAP 预处理版 128 Hz 通道顺序 baseline", k=3)
    assert next(row for row in deap["chunks"] if row["retrieval_role"] == "match")["doc_id"] == "dataset_deap"
    faa = idx.search("额叶 alpha 不对称 参考方式 F4 F3", k=3)
    assert next(row for row in faa["chunks"] if row["retrieval_role"] == "match")["doc_id"] == "methods_spectral"
    only_devices = idx.search("Emotiv 采样率 通道", k=5, domain="devices")
    assert only_devices["chunks"] and all(c["domain"] == "devices" for c in only_devices["chunks"])
    got = idx.get(top["chunks"][0]["chunk_id"])
    assert got and got["text"] == top["chunks"][0]["text"]
    assert idx.get("k_nonexist") is None


def test_seed_graph_is_consistent():
    idx = _index()
    g = idx.graph
    st = g.stats()
    assert st["n_entities"] >= 60 and st["n_relations"] >= 70
    bad = [w for w in g.warnings if "unknown entity" in w or "unknown relation" in w or "unknown entity type" in w]
    assert not bad, bad
    unresolved = [r for r in g.relations.values() if r.unresolved]
    assert not unresolved, [(r.src, r.rel, r.dst, r.evidence) for r in unresolved]
    for e in g.entities.values():
        assert e.type in ENTITY_TYPES
        assert e.chunk_ids, f"entity {e.id} has no resolvable refs"
    for r in g.relations.values():
        assert r.rel in RELATIONS


def test_graph_queries_and_expansion():
    idx = _index()
    g = idx.graph
    camer = g.entity("CAMER")
    assert camer and camer.id == "model_camer"
    rels = {(r.rel, r.dst) for r in g.neighbors("model_camer")}
    assert ("requires_input", "method_de") in rels and ("evaluated_on", "dataset_amigos") in rels
    chain = g.path("device_emotiv_epoc", "model_camer")
    assert chain and len(chain) <= 3
    res = idx.search("Emotiv EPOC 采的数据能不能喂给 CAMER", k=4)
    ids = {e["id"] for e in res["entities"]}
    assert "model_camer" in ids and "device_emotiv_epoc" in ids
    assert any(f["src"]["id"] == "model_camer" and f["rel"] == "requires_input" for f in res["facts"])
    assert all(f["evidence"] for f in res["facts"])
    assert g.entity("libeer_filtfilt").id == "method_de_libeer"
    assert g.link_text("这段 DE 用的是 TorchEEG 实现") and "method_de_torcheeg" in g.link_text("torcheeg_var 四频带")


def test_chunks_have_sources_and_stable_ids(tmp_path: Path):
    doc = tmp_path / "a" / "x.md"
    doc.parent.mkdir()
    doc.write_text("---\ndoc_id: t\ntitle: T\nsource: S\ndomain: methods\ntasks: [a, b]\n---\n\n## 一\n\n内容一\n\n## 二\n\n内容二\n", encoding="utf-8")
    a = KnowledgeIndex(tmp_path, embedder=None, reranker=None)
    b = KnowledgeIndex(tmp_path, embedder=None, reranker=None)
    assert [c.chunk_id for c in a.chunks] == [c.chunk_id for c in b.chunks]
    assert {c.section for c in a.chunks} == {"一", "二"}
    assert a.chunks[0].source == "S" and a.chunks[0].tasks == ["a", "b"]
    assert a.graph.stats()["n_entities"] == 0


def test_evidence_resolution_is_fuzzy(tmp_path: Path):
    (tmp_path / "n").mkdir()
    (tmp_path / "graph").mkdir()
    (tmp_path / "n" / "d.md").write_text("---\ndoc_id: d\ntitle: D\n---\n\n## 三种实现，数值互不相容\n\n正文\n", encoding="utf-8")
    (tmp_path / "graph" / "g.yaml").write_text(
        "entities:\n  - {id: method_x, type: method, name: X, refs: [d#三种实现]}\n  - {id: method_y, type: method, name: Y, refs: [d]}\n"
        "relations:\n  - {src: method_x, rel: variant_of, dst: method_y, evidence: [d#三种实现，数值互不相容]}\n", encoding="utf-8")
    idx = KnowledgeIndex(tmp_path, embedder=None, reranker=None)
    g = idx.graph
    assert g.entities["method_x"].chunk_ids and g.entities["method_y"].chunk_ids
    (r,) = g.relations.values()
    assert not r.unresolved and r.chunk_ids == g.entities["method_x"].chunk_ids


def test_ingest_text_to_markdown(tmp_path: Path):
    from eegagent.knowledge.ingest import ingest_file

    txt = tmp_path / "paper.txt"
    txt.write_text("A Great Paper Title Here\nAbstract\nWe propose differential entropy.\n1 Introduction\nEEG is\nnoisy.\n2 Methods\nWe use Welch.\nReferences\n[1] foo\n", encoding="utf-8")
    out = ingest_file(txt, doc_id="paper_test", title="A Great Paper", out_dir=tmp_path / "papers", year="2013", tasks=["a"])
    md = out.read_text(encoding="utf-8")
    assert md.startswith("---\ndoc_id: paper_test\ntitle: A Great Paper\ndoc_type: paper")
    assert "## Abstract" in md and "## 1 Introduction" in md and "## 2 Methods" in md
    assert "## References" in md and "[1] foo" in md and "EEG is noisy." in md
    idx = KnowledgeIndex(tmp_path, embedder=None, reranker=None)
    assert any(c.doc_type == "paper" and c.doc_id == "paper_test" for c in idx.chunks)


def test_fake_embedder_hybrid_and_cache(tmp_path: Path, monkeypatch):
    import numpy as np

    from eegagent.knowledge.embed import BaseEmbedder, VectorCache

    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))

    class Fake(BaseEmbedder):
        name = "fake"
        dim = 8
        calls = 0

        def encode(self, texts):
            Fake.calls += 1
            out = np.zeros((len(texts), 8), dtype=np.float32)
            for i, t in enumerate(texts):
                for tok in tokenize(t):
                    out[i, hash(tok) % 8] += 1
            return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)

    idx = KnowledgeIndex(knowledge_dir(), embedder=Fake(), reranker=None)
    assert idx.vectors is not None and idx.vectors.shape[0] == len(idx.chunks)
    first_calls = Fake.calls
    idx2 = KnowledgeIndex(knowledge_dir(), embedder=Fake(), reranker=None)
    assert Fake.calls == first_calls  # served from cache
    assert idx2.search("差分熵 定义", k=3)["chunks"]
    vc = VectorCache("fake")
    assert vc.meta and len(vc.vectors) == len(idx.chunks)
