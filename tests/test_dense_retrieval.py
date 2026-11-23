"""Remote/dense retrieval plumbing: API embedder, incremental cache, background build,
rerank blend, circuit breakers and session-document vectors — stubs only, no network."""
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
import requests

from eegagent.knowledge import documents
from eegagent.knowledge.bm25 import BM25
from eegagent.knowledge.embed import APIReranker, OpenAIEmbedder, VectorCache, embed_missing, make_reranker, sha
from eegagent.knowledge.index import KnowledgeIndex
from eegagent.knowledge.tokenize import tokenize


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    for name in ("EEGAGENT_EMBED_PROVIDER", "EEGAGENT_EMBED_BASE_URL", "EEGAGENT_EMBED_MODEL",
                 "EEGAGENT_RERANK_BASE_URL", "EEGAGENT_RERANK_MODEL", "EEGAGENT_RERANK_MODE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def kroot(tmp_path):
    """Knowledge root kept separate from EEGAGENT_STORAGE_DIR: Chunk.row() materialises
    document_text under <storage>/knowledge_sources and a same-dir root would re-ingest it."""
    root = tmp_path / "kroot"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def _reset_shared():
    documents.reset_shared_embedder()
    yield
    documents.reset_shared_embedder()


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _vectors(texts, dim=4):
    return {"data": [{"index": i, "embedding": [0.5] * dim} for i in range(len(texts))]}


def _write_doc(root, doc_id, sections):
    body = "\n".join(f"## {title}\n\n{text}\n" for title, text in sections)
    (root / "n").mkdir(exist_ok=True)
    (root / "n" / f"{doc_id}.md").write_text(f"---\ndoc_id: {doc_id}\ntitle: {doc_id}\n---\n\n{body}", encoding="utf-8")


# ---------------------------------------------------------------- remote embedder


def test_openai_embedder_dimensions_instruction_memo_and_retry(storage, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    calls = []
    queue = [_Resp(503)]

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return queue.pop(0) if queue else _Resp(payload=_vectors(json["input"], json.get("dimensions") or 4))

    monkeypatch.setattr(requests, "post", fake_post)
    emb = OpenAIEmbedder("https://x.test/v1/", "k9", "qwen3-emb", dimensions=8, query_instruction="Q: ")
    assert emb.name == "openai:qwen3-emb@8"

    emb.encode(["a", "b"])
    assert calls[0]["json"]["dimensions"] == 8 and calls[0]["json"]["input"] == ["a", "b"]
    assert calls[0]["timeout"] == 120 and calls[0]["headers"]["Authorization"] == "Bearer k9"
    assert len(calls) == 2  # 503 retried once, then 200

    v1 = emb.encode_query("hello")
    assert calls[2]["json"]["input"] == ["Q: hello"] and calls[2]["timeout"] == 15
    v2 = emb.encode_query("hello")
    assert len(calls) == 3  # memoised: no extra request
    assert np.allclose(v1, v2) and np.isclose(np.linalg.norm(v1), 1.0)


def test_openai_embedder_client_error_includes_body(storage, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _Resp(400, text="bad dimensions" + "x" * 300))
    emb = OpenAIEmbedder("https://x.test/v1", "", "m")
    with pytest.raises(RuntimeError, match="bad dimensions"):
        emb.encode(["a"])


# ---------------------------------------------------------------- incremental cache


class _FailOnBatch:
    name = "fake:fail"

    def __init__(self, fail_batch):
        self.calls = 0
        self.fail_batch = fail_batch

    def encode(self, texts):
        self.calls += 1
        if self.calls == self.fail_batch:
            raise RuntimeError("api down")
        return np.ones((len(texts), 4), dtype=np.float32)


def test_embed_missing_saves_cache_incrementally(storage):
    chunks = [SimpleNamespace(chunk_id=f"c{i}", index_text=f"t {i}", text_sha=sha(f"t {i}")) for i in range(320)]
    emb = _FailOnBatch(fail_batch=10)
    with pytest.raises(RuntimeError):
        embed_missing(emb, chunks, VectorCache(emb.name))
    fresh = VectorCache(emb.name)
    assert len(fresh.meta) == 8 * 32
    assert set(fresh.vectors) == {c.chunk_id for c in chunks[:256]}


# ---------------------------------------------------------------- background build


def _toy_vector(text):
    v = np.zeros(4, dtype=np.float32)
    if "zebra" in text or "rainbow" in text:
        v[0] = 1.0
    if "table" in text:
        v[1] = 1.0
    return v


def test_background_dense_build_keeps_search_sparse_then_dense(storage, kroot):
    _write_doc(kroot, "d1", [("斑马", "zebra stripes pattern"), ("表格", "ordinary table notes")])
    gate = threading.Event()

    class BlockingToy:
        name = "fake:block"
        def encode(self, texts):
            gate.wait(30)
            return np.asarray([_toy_vector(t) for t in texts], dtype=np.float32)
        def encode_query(self, text):
            return _toy_vector(text)

    idx = KnowledgeIndex(kroot, embedder=BlockingToy(), reranker=None, background_embed=True)
    assert idx.vectors is None and not idx.stats()["dense_ready"]
    blocked = idx.search("rainbow", k=3)  # sparse+graph while the build thread is parked
    assert isinstance(blocked["chunks"], list)
    gate.set()
    for _ in range(400):
        if idx.dense_state["ready"]:
            break
        time.sleep(0.025)
    assert idx.dense_state["ready"] and idx.vectors is not None
    assert idx.stats()["dense_ready"] and idx.dense_state["error"] is None
    assert idx.stats()["dense_progress"] == {"embedded": 2, "total": 2, "error": None}
    rows = idx.search("rainbow", k=3)["chunks"]  # no lexical overlap: dense retrieval only
    matches = [r for r in rows if r["retrieval_role"] == "match"]
    assert matches and "zebra" in matches[0]["text"]


# ---------------------------------------------------------------- rerank modes


class _FixedReranker:
    name = "fake:rr"

    def scores(self, query, texts):
        return [0.2, 0.9, 0.5, 0.1]


def test_rerank_blend_and_replace_orders(storage, kroot, monkeypatch):
    _write_doc(kroot, "d1", [("s0", "zebra"), ("s1", "zebra " + "filler " * 4),
                                ("s2", "zebra " + "filler " * 9), ("s3", "zebra " + "filler " * 14)])
    monkeypatch.setenv("EEGAGENT_RERANK_MODE", "blend")
    idx = KnowledgeIndex(kroot, embedder=None, reranker=_FixedReranker())
    ids = [c.chunk_id for c in idx.chunks]
    rows = [r for r in idx.search("zebra", k=4)["chunks"] if r["retrieval_role"] == "match"]
    assert [r["chunk_id"] for r in rows] == [ids[1], ids[0], ids[2], ids[3]]
    assert rows[0]["score"] == round(1 / 12 + 1 / 11, 4)

    monkeypatch.setenv("EEGAGENT_RERANK_MODE", "replace")
    idx = KnowledgeIndex(kroot, embedder=None, reranker=_FixedReranker())
    rows = [r for r in idx.search("zebra", k=4)["chunks"] if r["retrieval_role"] == "match"]
    assert [r["chunk_id"] for r in rows] == [ids[1], ids[2], ids[0], ids[3]]
    assert rows[0]["score"] == 0.9 and rows[-1]["score"] == 0.1


# ---------------------------------------------------------------- resilience


def test_dense_failure_falls_back_and_breaks_circuit(storage, kroot):
    _write_doc(kroot, "d1", [("斑马", "zebra stripes"), ("表格", "table notes")])

    class RaisingQuery:
        name = "fake:raise"

        def __init__(self):
            self.query_calls = 0

        def encode(self, texts):
            return np.ones((len(texts), 4), dtype=np.float32)

        def encode_query(self, text):
            self.query_calls += 1
            raise RuntimeError("embed api down")

    emb = RaisingQuery()
    idx = KnowledgeIndex(kroot, embedder=emb, reranker=None)
    assert idx.search("zebra", k=2)["chunks"]
    assert emb.query_calls == 1
    assert idx.search("zebra", k=2)["chunks"]
    assert emb.query_calls == 1  # circuit open within the cooldown


def test_reranker_failure_falls_back_and_breaks_circuit(storage, kroot):
    _write_doc(kroot, "d1", [("斑马", "zebra stripes"), ("表格", "table notes")])

    class RaisingReranker:
        name = "fake:rr"

        def __init__(self):
            self.calls = 0

        def scores(self, query, texts):
            self.calls += 1
            raise RuntimeError("rerank api down")

    rr = RaisingReranker()
    idx = KnowledgeIndex(kroot, embedder=None, reranker=rr)
    assert idx.search("zebra", k=2)["chunks"]
    assert idx.search("zebra", k=2)["chunks"]
    assert rr.calls == 1


# ---------------------------------------------------------------- api reranker


def test_api_reranker_request_mapping_retry_and_env(storage, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    calls = []
    queue = [_Resp(503)]

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if queue:
            return queue.pop(0)
        return _Resp(payload={"results": [{"index": 2, "relevance_score": 0.7},
                                          {"index": 1, "relevance_score": -0.2},
                                          {"index": 0, "relevance_score": 0.3}]})

    monkeypatch.setattr(requests, "post", fake_post)
    rr = APIReranker("https://r.test/v1/", "key1", "r-m")
    assert rr.name == "api:r-m"
    assert rr.scores("q", ["a", "b", "c"]) == [0.3, -0.2, 0.7]
    assert len(calls) == 2  # one retry after 503
    assert calls[-1]["url"] == "https://r.test/v1/rerank"
    assert calls[-1]["json"] == {"model": "r-m", "query": "q", "documents": ["a", "b", "c"], "top_n": 3}
    assert calls[-1]["headers"]["Authorization"] == "Bearer key1"

    monkeypatch.setenv("EEGAGENT_RERANK_BASE_URL", "https://r.test/v1")
    monkeypatch.setenv("EEGAGENT_RERANK_MODEL", "r-m")
    monkeypatch.delenv("EEGAGENT_RERANK_API_KEY", raising=False)
    monkeypatch.setenv("EEGAGENT_EMBED_API_KEY", "embed-key")
    made = make_reranker()
    assert isinstance(made, APIReranker) and made.api_key == "embed-key"


# ---------------------------------------------------------------- session documents


class _DocEmbedder:
    name = "fake:doc"

    def encode(self, texts):
        return np.asarray([self._v(t) for t in texts], dtype=np.float32)

    def encode_query(self, text):
        return self._v(text)

    @staticmethod
    def _v(t):
        v = np.zeros(4, dtype=np.float32)
        if "seizure" in t.lower() or "ictal" in t.lower():
            v[0] = 1.0
        if "electrode" in t.lower() or "montage" in t.lower():
            v[1] = 1.0
        return v


def test_session_document_vectors_dense_retrieval(storage, tmp_path, monkeypatch, commit_session):
    path = tmp_path / "notes.txt"
    path.write_text("seizure onset zone localization methods\n\nsecond paragraph about scalp electrodes\n",
                    encoding="utf-8")
    documents.parse_document(path, "doc_s1", "ses_a")
    fake = _DocEmbedder()
    monkeypatch.setattr(documents, "_shared_embedder", fake)
    target = documents.embed_document("doc_s1", "ses_a")
    assert target.exists()
    commit_session("ses_a", manifests=[{"manifest_id": "doc_s1", "modality": "document"}])

    rows = documents.search_documents("ictal discharges", "ses_a", 3)  # no lexical overlap
    assert rows and "seizure" in rows[0]["text"]

    with np.load(target) as z:
        ids, vectors = z["ids"], z["vectors"]
    with target.open("wb") as f:
        np.savez(f, ids=ids, vectors=vectors, embedder=np.array("other:model"))
    assert documents.document_vectors("doc_s1", "ses_a") is None  # stale embedder name ignored

    monkeypatch.setattr(documents, "_shared_embedder", None)
    chunks = documents.session_chunks("ses_a")
    tokens = [tokenize(f"{r['title']} {r['section']} {r['text']}") for r in chunks]
    expected = [{**chunks[i], "score": round(s, 4)} for i, s in BM25(tokens).top(tokenize("seizure electrodes"), 3)]
    assert documents.search_documents("seizure electrodes", "ses_a", 3) == expected


# ---------------------------------------------------------------- malformed responses


def _fake_post(recorder, payload):
    def post(url, json=None, headers=None, timeout=None):
        recorder.append(json)
        return _Resp(payload=payload)
    return post


@pytest.mark.parametrize("data", [
    [{"index": 0, "embedding": [0.5] * 8}],                                           # missing index
    [{"index": 0, "embedding": [0.5] * 8}, {"index": 0, "embedding": [0.5] * 8}],      # duplicate index
    [{"index": 0, "embedding": [0.5] * 4}, {"index": 1, "embedding": [0.5] * 4}],      # dims != configured 8
    [{"index": 0, "embedding": [0.5] * 8}, {"index": 1, "embedding": [np.nan] * 8}],   # non-finite
])
def test_embeddings_malformed_responses_rejected(storage, monkeypatch, data):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    calls = []
    monkeypatch.setattr(requests, "post", _fake_post(calls, {"data": data}))
    emb = OpenAIEmbedder("https://x.test/v1", "", "m", dimensions=8)
    with pytest.raises(ValueError):
        emb.encode(["a", "b"])
    assert len(calls) == 1  # a malformed 200 is not transient: no retry


@pytest.mark.parametrize("results", [
    [{"index": 0, "relevance_score": 0.3}, {"index": 2, "relevance_score": 0.7}],       # missing index 1
    [{"index": 0, "relevance_score": 0.3}, {"index": 0, "relevance_score": 0.4},
     {"index": 1, "relevance_score": 0.5}, {"index": 2, "relevance_score": 0.6}],       # duplicate index 0
    [{"index": 0, "relevance_score": 0.3}, {"index": 1, "relevance_score": 0.4},
     {"index": 5, "relevance_score": 0.6}],                                           # out of range
    [{"index": 0, "relevance_score": 0.3}, {"index": 1, "relevance_score": 0.4},
     {"index": 2, "relevance_score": np.nan}],                                        # non-finite
])
def test_rerank_malformed_responses_rejected(storage, monkeypatch, results):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    calls = []
    monkeypatch.setattr(requests, "post", _fake_post(calls, {"results": results}))
    rr = APIReranker("https://r.test/v1", "key", "m")
    with pytest.raises(ValueError):
        rr.scores("q", ["a", "b", "c"])
    assert len(calls) == 1


class _ShortBatch:
    name = "fake:short"

    def encode(self, texts):
        return np.ones((len(texts) - 1, 4), dtype=np.float32)


def test_embed_missing_rejects_short_batch_without_writing(storage):
    chunks = [SimpleNamespace(chunk_id=f"c{i}", index_text=f"t {i}", text_sha=sha(f"t {i}")) for i in range(4)]
    with pytest.raises(ValueError, match="does not match"):
        embed_missing(_ShortBatch(), chunks, VectorCache(_ShortBatch.name))
    assert VectorCache(_ShortBatch.name).meta == {}


def test_mismatched_query_dimension_falls_back_and_breaks_circuit(storage, kroot):
    _write_doc(kroot, "d1", [("斑马", "zebra stripes"), ("表格", "table notes")])

    class DimMismatch:
        name = "fake:dim"

        def __init__(self):
            self.query_calls = 0

        def encode(self, texts):
            return np.ones((len(texts), 4), dtype=np.float32)

        def encode_query(self, text):
            self.query_calls += 1
            return np.ones(3, dtype=np.float32)

    emb = DimMismatch()
    idx = KnowledgeIndex(kroot, embedder=emb, reranker=None)
    sparse = [r["chunk_id"] for r in KnowledgeIndex(kroot, embedder=None, reranker=None)
              .search("zebra", k=2)["chunks"] if r["retrieval_role"] == "match"]
    for _ in range(2):
        rows = [r["chunk_id"] for r in idx.search("zebra", k=2)["chunks"] if r["retrieval_role"] == "match"]
        assert rows == sparse
    assert emb.query_calls == 1


# ---------------------------------------------------------------- cache durability


def test_embed_missing_progress_counts_cached_and_new(storage):
    chunks = [SimpleNamespace(chunk_id=f"c{i}", index_text=f"t {i}", text_sha=sha(f"t {i}")) for i in range(5)]
    cache = VectorCache("fake:resume")
    for c in chunks[:2]:
        cache.put(c.chunk_id, c.text_sha, np.ones(4, dtype=np.float32))
    cache.save()
    progress = []
    embed_missing(_FailOnBatch(fail_batch=99), chunks, cache, on_progress=lambda d, t: progress.append((d, t)))
    assert progress == [(5, 5)]  # one batch; counts the 2 cached + 3 new


def test_vector_cache_embedded_meta_survives_stale_json_and_legacy_loads(storage):
    import json
    cache = VectorCache("fake:meta")
    cache.put("c1", "sha1", np.ones(4, dtype=np.float32))
    cache.save()
    cache.base.with_suffix(".json").write_text('{"stale": true}', encoding="utf-8")
    fresh = VectorCache("fake:meta")
    assert fresh.meta == {"c1": "sha1"} and set(fresh.vectors) == {"c1"}
    legacy = VectorCache("fake:legacy")
    np.savez_compressed(legacy.base.with_suffix(".npz"), c2=np.ones(4, dtype=np.float32))
    legacy.base.with_suffix(".json").write_text(json.dumps({"c2": "sha2"}), encoding="utf-8")
    again = VectorCache("fake:legacy")
    assert again.meta == {"c2": "sha2"} and set(again.vectors) == {"c2"}


def test_vector_cache_replace_failure_keeps_previous_snapshot(storage, monkeypatch):
    import os
    cache = VectorCache("fake:atomic")
    cache.put("c1", "sha1", np.ones(4, dtype=np.float32))
    cache.save()
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk busy")))
    cache.put("c2", "sha2", np.ones(4, dtype=np.float32))
    with pytest.raises(OSError):
        cache.save()
    assert set(VectorCache("fake:atomic").vectors) == {"c1"}


# ---------------------------------------------------------------- session document resilience


def test_document_dense_never_queries_without_vectors(storage, tmp_path, monkeypatch, commit_session):
    calls = []

    class Counting(_DocEmbedder):
        name = "fake:count"

        def encode_query(self, text):
            calls.append(text)
            return self._v(text)

    emb = Counting()
    monkeypatch.setattr(documents, "_shared_embedder", emb)
    monkeypatch.setattr(documents, "ensure_document_vectors", lambda *a, **kw: None)
    assert documents.search_documents("anything", "ses_empty", 3) == []
    path = tmp_path / "notes.txt"
    path.write_text("seizure onset zone notes\n", encoding="utf-8")
    documents.parse_document(path, "doc_nv", "ses_b")
    commit_session("ses_b", manifests=[{"manifest_id": "doc_nv", "modality": "document"}])
    assert documents.search_documents("seizure", "ses_b", 3) and calls == []


def test_document_query_failure_breaks_circuit(storage, tmp_path, monkeypatch, commit_session):
    path = tmp_path / "notes.txt"
    path.write_text("seizure onset zone localization methods\n", encoding="utf-8")
    documents.parse_document(path, "doc_f", "ses_a")
    monkeypatch.setattr(documents, "_shared_embedder", _DocEmbedder())
    documents.embed_document("doc_f", "ses_a")
    commit_session("ses_a", manifests=[{"manifest_id": "doc_f", "modality": "document"}])
    calls = []

    class Failing(_DocEmbedder):
        name = "fake:doc"

        def encode_query(self, text):
            calls.append(text)
            raise RuntimeError("api down")

    monkeypatch.setattr(documents, "_shared_embedder", Failing())
    monkeypatch.setattr(documents, "ensure_document_vectors", lambda *a, **kw: None)
    for _ in range(2):
        rows = documents.search_documents("seizure", "ses_a", 3)
        assert rows and "seizure" in rows[0]["text"]
    assert len(calls) == 1


def test_document_vectors_rejects_invalid_sidecar(storage, tmp_path, monkeypatch):
    path = tmp_path / "notes.txt"
    path.write_text("seizure onset zone localization methods\n", encoding="utf-8")
    documents.parse_document(path, "doc_bad", "ses_a")
    monkeypatch.setattr(documents, "_shared_embedder", _DocEmbedder())
    target = documents.embed_document("doc_bad", "ses_a")
    with np.load(target) as z:
        ids, vectors = z["ids"], z["vectors"].copy()
    vectors[0][0] = np.nan
    with target.open("wb") as f:
        np.savez(f, ids=ids, vectors=vectors, embedder=np.array("fake:doc"))
    assert documents.document_vectors("doc_bad", "ses_a") is None


def test_empty_document_spawns_no_embedding_job(storage, tmp_path, monkeypatch):
    path = tmp_path / "empty.txt"
    path.write_text("   \n", encoding="utf-8")
    documents.parse_document(path, "doc_e", "ses_a")
    monkeypatch.setattr(documents, "_shared_embedder", _DocEmbedder())
    calls = []
    monkeypatch.setattr(documents, "embed_document", lambda *a: calls.append(a) or None)
    documents.ensure_document_vectors("doc_e", "ses_a")
    assert ("ses_a", "doc_e") not in documents._vec_jobs and not calls
