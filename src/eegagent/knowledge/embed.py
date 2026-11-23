"""Pluggable embedding / reranking providers with a disk cache.

Env:
  EEGAGENT_EMBED_PROVIDER   none | sentence_transformers | openai   (auto-detected if unset)
  EEGAGENT_EMBED_MODEL      local path or hub name (sentence_transformers) / model id (openai)
  EEGAGENT_EMBED_BASE_URL   OpenAI-compatible base url, e.g. https://api.example.com/v1
  EEGAGENT_EMBED_API_KEY
  EEGAGENT_EMBED_DIMENSIONS          optional output size sent as "dimensions" (openai)
  EEGAGENT_EMBED_QUERY_INSTRUCTION   optional query-side prefix; a literal \n becomes a newline
  EEGAGENT_RERANK_MODEL     cross-encoder path/name (sentence_transformers) or API model id, optional
  EEGAGENT_RERANK_BASE_URL  rerank API base url; when set, EEGAGENT_RERANK_MODEL is sent to it
  EEGAGENT_RERANK_API_KEY   defaults to EEGAGENT_EMBED_API_KEY
  EEGAGENT_STORAGE_DIR      cache lives in <storage>/knowledge_index/
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import numpy as np


class BaseEmbedder:
    name = "none"
    dim = 0

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def encode_query(self, text: str) -> np.ndarray:
        """1-D vector for one query; providers may use a cheaper path or an instruction prefix."""
        return self.encode([text])[0]


class SentenceTransformersEmbedder(BaseEmbedder):
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model_name = model
        self.name = "st:" + Path(model).name
        self._m = SentenceTransformer(model)
        self.dim = int(self._m.get_sentence_embedding_dimension() or 0)

    def encode(self, texts: list[str]) -> np.ndarray:
        v = self._m.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return np.asarray(v, dtype=np.float32)


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI-compatible /embeddings client with retries and a memoised query path."""

    def __init__(self, base_url: str, api_key: str, model: str, batch: int = 32,
                 dimensions: int | None = None, query_instruction: str = ""):
        import requests  # optional dependency

        self._rq = requests
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model
        self.name = "openai:" + model + (f"@{dimensions}" if dimensions else "")
        self.batch = batch
        self.dimensions = dimensions
        self.query_instruction = query_instruction
        self._query_cache: dict[str, np.ndarray] = {}
        self.dim = dimensions or 0

    def _request(self, texts: list[str], timeout: float, attempts: int) -> list[list[float]]:
        body: dict = {"model": self.model_name, "input": texts}
        if self.dimensions:
            body["dimensions"] = self.dimensions
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        for attempt in range(attempts):
            try:
                r = self._rq.post(f"{self.base_url}/embeddings", json=body, headers=headers, timeout=timeout)
                if r.status_code < 400:
                    data = r.json()["data"]
                    indices = [row["index"] for row in data]
                    if (len(data) != len(texts) or any(type(i) is not int for i in indices)
                            or sorted(indices) != list(range(len(texts)))):
                        raise ValueError("embeddings response has missing or duplicate indices")
                    data = sorted(data, key=lambda row: row["index"])
                    vectors = np.asarray([row["embedding"] for row in data], dtype=np.float32)
                    if (vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] == 0
                            or not np.isfinite(vectors).all() or (self.dim and vectors.shape[1] != self.dim)):
                        raise ValueError("embeddings response has invalid vector shape or values")
                    self.dim = vectors.shape[1]
                    return vectors.tolist()
                if r.status_code == 429 or r.status_code >= 500:
                    raise self._rq.RequestException(f"embeddings http {r.status_code}")
                raise RuntimeError(f"embeddings http {r.status_code}: {r.text[:200]}")
            except self._rq.RequestException:
                if attempt + 1 >= attempts:
                    raise
                time.sleep((2, 5)[min(attempt, 1)])
        raise RuntimeError("unreachable")

    @staticmethod
    def _normalized(v: np.ndarray) -> np.ndarray:
        v = np.asarray(v, dtype=np.float32)
        if v.ndim == 2 and v.size:
            v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
        return v

    def encode(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            out.extend(self._request(texts[i:i + self.batch], timeout=120, attempts=3))
        v = self._normalized(np.asarray(out, dtype=np.float32))
        if v.ndim == 2 and v.size:
            self.dim = v.shape[1]
        return v

    def encode_query(self, text: str) -> np.ndarray:
        cached = self._query_cache.get(text)
        if cached is not None:
            return cached
        v = np.asarray(self._request([self.query_instruction + text], timeout=15, attempts=2)[0], dtype=np.float32)
        v /= max(np.linalg.norm(v), 1e-9)
        if len(self._query_cache) >= 256:
            self._query_cache.pop(next(iter(self._query_cache)))
        self._query_cache[text] = v
        return v


def make_embedder() -> BaseEmbedder | None:
    provider = os.environ.get("EEGAGENT_EMBED_PROVIDER", "").strip().lower()
    model = os.environ.get("EEGAGENT_EMBED_MODEL", "").strip()
    base = os.environ.get("EEGAGENT_EMBED_BASE_URL", "").strip()
    if not provider:
        provider = "openai" if base else ("sentence_transformers" if model else "none")
    try:
        if provider == "sentence_transformers" and model:
            return SentenceTransformersEmbedder(model)
        if provider == "openai" and base and model:
            dims = os.environ.get("EEGAGENT_EMBED_DIMENSIONS", "").strip()
            instruction = os.environ.get("EEGAGENT_EMBED_QUERY_INSTRUCTION", "").replace("\\n", "\n")
            return OpenAIEmbedder(base, os.environ.get("EEGAGENT_EMBED_API_KEY", ""), model,
                                  dimensions=int(dims) if dims else None, query_instruction=instruction)
    except Exception as exc:  # noqa: BLE001
        print(f"[knowledge] embedder unavailable ({provider}): {exc}")
    return None


class Reranker:
    def __init__(self, model: str):
        from sentence_transformers import CrossEncoder  # optional dependency

        self.name = "ce:" + Path(model).name
        self._m = CrossEncoder(model)

    def scores(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        s = self._m.predict([(query, t) for t in texts], show_progress_bar=False)
        return [float(x) for x in np.asarray(s).ravel()]


class APIReranker:
    """Remote /rerank endpoint client (Jina/Cohere/OpenAI-compatible schema)."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 20):
        import requests  # optional dependency

        self._rq = requests
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model
        self.name = "api:" + model
        self.timeout = timeout

    def scores(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        body = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        for attempt in range(2):
            try:
                r = self._rq.post(f"{self.base_url}/rerank", json=body, headers=headers, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    raise self._rq.RequestException(f"rerank http {r.status_code}")
                if r.status_code >= 400:
                    raise RuntimeError(f"rerank http {r.status_code}: {r.text[:200]}")
                results = r.json()["results"]
                indices = [row["index"] for row in results]
                if (len(results) != len(texts) or any(type(i) is not int for i in indices)
                        or sorted(indices) != list(range(len(texts)))):
                    raise ValueError("rerank response has missing or duplicate indices")
                scores = [float(row["relevance_score"]) for row in sorted(results, key=lambda row: row["index"])]
                if not np.isfinite(np.asarray(scores, dtype=np.float64)).all():
                    raise ValueError("rerank response has non-finite scores")
                return scores
            except self._rq.RequestException:
                if attempt:
                    raise
                time.sleep(2)
        raise RuntimeError("unreachable")


def make_reranker() -> Reranker | "APIReranker" | None:
    model = os.environ.get("EEGAGENT_RERANK_MODEL", "").strip()
    if not model:
        return None
    base = os.environ.get("EEGAGENT_RERANK_BASE_URL", "").strip()
    if base:
        key = os.environ.get("EEGAGENT_RERANK_API_KEY") or os.environ.get("EEGAGENT_EMBED_API_KEY", "")
        return APIReranker(base, key, model)
    try:
        return Reranker(model)
    except Exception as exc:  # noqa: BLE001
        print(f"[knowledge] reranker unavailable: {exc}")
        return None


# ---------------------------------------------------------------- cache

def _cache_dir() -> Path:
    root = Path(os.environ.get("EEGAGENT_STORAGE_DIR", Path(__file__).resolve().parents[3] / "storage"))
    d = root / "knowledge_index"
    d.mkdir(parents=True, exist_ok=True)
    return d


_cache_save_lock = threading.Lock()


class VectorCache:
    """chunk_id -> (text_sha, vector), persisted per embedder as npz + json."""

    def __init__(self, embedder_name: str):
        slug = re.sub(r"[^a-z0-9]+", "_", embedder_name.lower()).strip("_") or "none"
        self.base = _cache_dir() / f"emb_{slug}"
        self.meta: dict[str, str] = {}
        self.vectors: dict[str, np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        with _cache_save_lock:
            mp, vp = self.base.with_suffix(".json"), self.base.with_suffix(".npz")
            if not vp.exists():
                return
            try:
                with np.load(vp) as z:
                    if "__meta__" in z.files:
                        self.meta = json.loads(str(z["__meta__"].item()))
                    elif mp.exists():
                        self.meta = json.loads(mp.read_text(encoding="utf-8"))
                    self.vectors = {k: z[k] for k in z.files if k != "__meta__"}
            except Exception:  # noqa: BLE001
                self.meta, self.vectors = {}, {}

    def save(self) -> None:
        """One atomic npz snapshot (metadata inside as __meta__) keeps text hashes and vectors
        consistent; the .json sidecar stays for compatibility and debugging only."""
        with _cache_save_lock:
            vp = self.base.with_suffix(".npz")
            if self.vectors:
                tmp = tempfile.NamedTemporaryFile(dir=vp.parent, prefix=vp.name + ".", delete=False)
                try:
                    with tmp:
                        np.savez_compressed(tmp, __meta__=np.array(json.dumps(self.meta)), **self.vectors)
                    os.replace(tmp.name, vp)
                except Exception:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
                    raise
            self.base.with_suffix(".json").write_text(json.dumps(self.meta), encoding="utf-8")

    def get(self, chunk_id: str, text_sha: str) -> np.ndarray | None:
        if self.meta.get(chunk_id) == text_sha:
            return self.vectors.get(chunk_id)
        return None

    def put(self, chunk_id: str, text_sha: str, vec: np.ndarray) -> None:
        self.meta[chunk_id] = text_sha
        self.vectors[chunk_id] = np.asarray(vec, dtype=np.float32)


def embed_missing(embedder: BaseEmbedder, chunks, cache: VectorCache, on_progress=None) -> None:
    """Encode cache misses in batches of 32; save() every 8 batches and at the end so an
    interrupted build keeps most of the corpus. on_progress(embedded, total) after each batch
    counts every covered chunk (cached plus newly embedded)."""
    missing = [c for c in chunks if cache.get(c.chunk_id, c.text_sha) is None]
    total = len(chunks)
    embedded = total - len(missing)
    starts = list(range(0, len(missing), 32))
    dim = len(next(iter(cache.vectors.values()), ()))
    for n, start in enumerate(starts):
        batch = missing[start:start + 32]
        enc = np.asarray(embedder.encode([c.index_text for c in batch]), dtype=np.float32)
        if enc.ndim != 2 or enc.shape[0] != len(batch) or enc.shape[1] == 0 or not np.isfinite(enc).all():
            raise ValueError("embedding batch does not match its chunks")
        if not dim:
            dim = enc.shape[1]
        elif enc.shape[1] != dim:
            raise ValueError("embedding batch dimension changed mid-build")
        for c, v in zip(batch, enc):
            cache.put(c.chunk_id, c.text_sha, v)
        embedded += len(batch)
        if (n + 1) % 8 == 0 or n == len(starts) - 1:
            cache.save()
        if on_progress:
            on_progress(embedded, total)


def embed_chunks(embedder: BaseEmbedder | None, chunks, use_cache: bool = True) -> np.ndarray | None:
    """Return a [n, dim] matrix aligned with chunks, encoding only cache misses. None if no embedder."""
    if embedder is None or not chunks:
        return None
    if not use_cache:
        return np.asarray(embedder.encode([c.index_text for c in chunks]), dtype=np.float32)
    cache = VectorCache(embedder.name)
    embed_missing(embedder, chunks, cache)
    missing = [c.chunk_id for c in chunks if cache.get(c.chunk_id, c.text_sha) is None]
    if missing:
        raise ValueError(f"vector cache is missing {len(missing)} chunks")
    return np.stack([cache.get(c.chunk_id, c.text_sha) for c in chunks])


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
