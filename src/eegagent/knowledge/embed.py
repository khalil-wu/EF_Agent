"""Dense embedding, reranking, incremental vector persistence and failure isolation.

Environment (EFAGENT_* is mapped to EEGAGENT_* by the launchers):
  EEGAGENT_EMBED_PROVIDER      none | sentence_transformers | openai
  EEGAGENT_EMBED_MODEL         local model path or OpenAI-compatible model id
  EEGAGENT_EMBED_BASE_URL      API root ending in /v1
  EEGAGENT_EMBED_API_KEY       API key (never logged)
  EEGAGENT_EMBED_DIMENSIONS    optional API output dimensions
  EEGAGENT_EMBED_QUERY_INSTRUCTION  prefix for queries only, not indexed passages
  EEGAGENT_EMBED_RETRIES       retry count for 429/5xx/transport failures (default 2)
  EEGAGENT_EMBED_BATCH         request/cache checkpoint batch size (default 64)
  EEGAGENT_RERANK_PROVIDER     cross_encoder | api (inferred from BASE_URL)
  EEGAGENT_RERANK_MODEL        optional local cross-encoder or API model
  EEGAGENT_RERANK_BASE_URL     OpenAI-compatible API root (POST /rerank)
  EEGAGENT_RERANK_API_KEY      API key
  EEGAGENT_RERANK_MODE         replace | blend (default replace)
  EEGAGENT_RERANK_BLEND_WEIGHT weight assigned to reranker in blend mode (default 0.5)
  EEGAGENT_STORAGE_DIR        cache stored in <storage>/knowledge_index/
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import numpy as np


def _normalized(v: np.ndarray) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    if a.ndim != 2 or not np.all(np.isfinite(a)):
        raise ValueError("embedding response must be a finite two-dimensional matrix")
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-9)


class BaseEmbedder:
    name = "none"
    dim = 0

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def encode_query(self, query: str) -> np.ndarray:
        """Query-specific encoding; by default identical to a passage encoding."""
        return self.encode([query])[0]


class SentenceTransformersEmbedder(BaseEmbedder):
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model_name = model
        self.name = "st:" + Path(model).name
        self._m = SentenceTransformer(model)
        self.dim = int(self._m.get_sentence_embedding_dimension() or 0)

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        return _normalized(self._m.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False))


class OpenAIEmbedder(BaseEmbedder):
    """POST /embeddings, preserving server indices and retrying transient failures.

    `encode_query` applies the instruction only at query time. The API model and
    requested dimensionality are part of the cache namespace.
    """

    def __init__(self, base_url: str, api_key: str, model: str, batch: int = 64,
                 retries: int = 2, dimensions: int | None = None, query_instruction: str = ""):
        import requests  # optional dependency

        self._rq = requests
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model
        self.batch = max(1, int(batch))
        self.retries = max(0, int(retries))
        self.dimensions = int(dimensions) if dimensions else None
        if self.dimensions is not None and self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        self.query_instruction = query_instruction
        self.name = f"openai:{model}" + (f":{self.dimensions}" if self.dimensions else "")
        self.dim = self.dimensions or 0

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        matrices: list[np.ndarray] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i:i + self.batch]
            payload = {"model": self.model_name, "input": chunk}
            if self.dimensions is not None:
                payload["dimensions"] = self.dimensions
            for attempt in range(self.retries + 1):
                try:
                    r = self._rq.post(
                        f"{self.base_url}/embeddings", json=payload,
                        headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                        timeout=120,
                    )
                    r.raise_for_status()
                    entries = r.json()["data"]
                    if len(entries) != len(chunk):
                        raise ValueError("embedding response count mismatch")
                    indexed = {int(row.get("index", j)): row["embedding"] for j, row in enumerate(entries)}
                    if set(indexed) != set(range(len(chunk))):
                        raise ValueError("embedding response indices mismatch")
                    vectors = _normalized(np.asarray([indexed[j] for j in range(len(chunk))], dtype=np.float32))
                    if vectors.shape[1] == 0 or (self.dim and vectors.shape[1] != self.dim):
                        raise ValueError("embedding response dimensions mismatch")
                    self.dim = int(vectors.shape[1])
                    matrices.append(vectors)
                    break
                except self._rq.HTTPError as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if attempt == self.retries or status not in (408, 409, 429, 500, 502, 503, 504):
                        raise
                    time.sleep(min(2 ** attempt, 8))
                except self._rq.RequestException:
                    if attempt == self.retries:
                        raise
                    time.sleep(min(2 ** attempt, 8))
        return np.vstack(matrices)

    def encode_query(self, query: str) -> np.ndarray:
        instruction = self.query_instruction.strip()
        text = f"{instruction}\n{query}" if instruction else query
        return self.encode([text])[0]


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
    """POST /rerank with ordered result indices (Jina/Cohere-style responses)."""

    def __init__(self, base_url: str, api_key: str, model: str, retries: int = 2):
        import requests  # optional dependency

        self._rq = requests
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.name = "api:" + model
        self.retries = max(0, int(retries))

    def scores(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        payload = {"model": self.model, "query": query, "documents": texts, "top_n": len(texts)}
        for attempt in range(self.retries + 1):
            try:
                r = self._rq.post(
                    f"{self.base_url}/rerank", json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    timeout=120,
                )
                r.raise_for_status()
                body = r.json()
                if isinstance(body.get("scores"), list):
                    scores = [float(x) for x in body["scores"]]
                else:
                    rows = body.get("results", body.get("data", []))
                    scores = [float("-inf")] * len(texts)
                    for row in rows:
                        pos = int(row["index"])
                        if pos < 0 or pos >= len(texts):
                            raise ValueError("rerank index out of bounds")
                        scores[pos] = float(row.get("relevance_score", row.get("score")))
                if len(scores) != len(texts) or not all(np.isfinite(x) for x in scores):
                    raise ValueError("rerank response must contain finite scores for all documents")
                return scores
            except self._rq.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if attempt == self.retries or status not in (408, 409, 429, 500, 502, 503, 504):
                    raise
                time.sleep(min(2 ** attempt, 8))
            except self._rq.RequestException:
                if attempt == self.retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError("reranking failed")  # pragma: no cover


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
            return OpenAIEmbedder(
                base, os.environ.get("EEGAGENT_EMBED_API_KEY", ""), model,
                batch=int(os.environ.get("EEGAGENT_EMBED_BATCH", "64")),
                retries=int(os.environ.get("EEGAGENT_EMBED_RETRIES", "2")),
                dimensions=int(os.environ["EEGAGENT_EMBED_DIMENSIONS"]) if os.environ.get("EEGAGENT_EMBED_DIMENSIONS") else None,
                query_instruction=os.environ.get("EEGAGENT_EMBED_QUERY_INSTRUCTION", ""),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[knowledge] embedder unavailable ({provider}): {exc}")
    return None


_EMBED_ENV = ("EEGAGENT_EMBED_PROVIDER", "EEGAGENT_EMBED_MODEL", "EEGAGENT_EMBED_BASE_URL",
              "EEGAGENT_EMBED_API_KEY", "EEGAGENT_EMBED_BATCH", "EEGAGENT_EMBED_RETRIES",
              "EEGAGENT_EMBED_DIMENSIONS", "EEGAGENT_EMBED_QUERY_INSTRUCTION")


@functools.lru_cache(maxsize=8)
def _cached_embedder(config: tuple[str, ...]) -> BaseEmbedder | None:
    return make_embedder()


def shared_embedder() -> BaseEmbedder | None:
    """One embedder for project and session indices; configuration changes invalidate it."""
    return _cached_embedder(tuple(os.environ.get(key, "") for key in _EMBED_ENV))


def make_reranker() -> Reranker | APIReranker | None:
    model = os.environ.get("EEGAGENT_RERANK_MODEL", "").strip()
    if not model:
        return None
    base = os.environ.get("EEGAGENT_RERANK_BASE_URL", "").strip()
    provider = os.environ.get("EEGAGENT_RERANK_PROVIDER", "").strip().lower() or ("api" if base else "cross_encoder")
    try:
        if provider in ("api", "openai") and base:
            return APIReranker(base, os.environ.get("EEGAGENT_RERANK_API_KEY", ""), model,
                               retries=int(os.environ.get("EEGAGENT_RERANK_RETRIES", "2")))
        if provider in ("cross_encoder", "sentence_transformers"):
            return Reranker(model)
    except Exception as exc:  # noqa: BLE001
        print(f"[knowledge] reranker unavailable ({provider}): {exc}")
    return None


class CircuitBreaker:
    """Per-provider open/half-open/closed guard with monotonic cooldown."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 60.0):
        self.threshold = max(1, int(threshold))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self.opened_at is None:
                return "closed"
            return "half_open" if time.monotonic() - self.opened_at >= self.cooldown_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


def _cache_dir() -> Path:
    root = Path(os.environ.get("EEGAGENT_STORAGE_DIR", Path(__file__).resolve().parents[3] / "storage"))
    d = root / "knowledge_index"
    d.mkdir(parents=True, exist_ok=True)
    return d


class VectorCache:
    """chunk_id -> (text_sha, vector), persisted per embedder as NPZ + JSON."""

    def __init__(self, embedder_name: str):
        slug = re.sub(r"[^a-z0-9]+", "_", embedder_name.lower()).strip("_") or "none"
        # Also hash the full model id to prevent filename collisions after slugification.
        self.base = _cache_dir() / f"emb_{slug[:70]}_{sha(embedder_name)[:10]}"
        self.meta: dict[str, str] = {}
        self.vectors: dict[str, np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        mp, vp = self.base.with_suffix(".json"), self.base.with_suffix(".npz")
        if mp.exists() and vp.exists():
            try:
                self.meta = json.loads(mp.read_text(encoding="utf-8"))
                with np.load(vp, allow_pickle=False) as z:
                    self.vectors = {k: z[k] for k in z.files}
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                self.meta, self.vectors = {}, {}

    def save(self) -> None:
        mp, vp = self.base.with_suffix(".json"), self.base.with_suffix(".npz")
        mtmp, vtmp = mp.with_suffix(".json.tmp"), vp.with_suffix(".npz.tmp")
        with vtmp.open("wb") as dst:
            np.savez_compressed(dst, **self.vectors)
        os.replace(vtmp, vp)
        mtmp.write_text(json.dumps(self.meta), encoding="utf-8")
        os.replace(mtmp, mp)

    def get(self, chunk_id: str, text_sha: str) -> np.ndarray | None:
        if self.meta.get(chunk_id) == text_sha:
            return self.vectors.get(chunk_id)
        return None

    def put(self, chunk_id: str, text_sha: str, vec: np.ndarray) -> None:
        self.meta[chunk_id] = text_sha
        self.vectors[chunk_id] = np.asarray(vec, dtype=np.float32)


def embed_missing(embedder: BaseEmbedder | None, chunks, use_cache: bool = True,
                  batch_size: int | None = None) -> np.ndarray | None:
    """Build an aligned matrix, checkpointing each successful batch to disk.

    On an API error, earlier batches remain cached; the exception propagates so
    callers can fall back to sparse retrieval without using a partial matrix.
    """
    if embedder is None or not chunks:
        return None
    cache = VectorCache(embedder.name) if use_cache else None
    vectors: list[np.ndarray | None] = []
    missing = []
    for i, chunk in enumerate(chunks):
        v = cache.get(chunk.chunk_id, chunk.text_sha) if cache else None
        vectors.append(v)
        if v is None:
            missing.append(i)
    size = max(1, int(batch_size or getattr(embedder, "batch", 64)))
    for start in range(0, len(missing), size):
        positions = missing[start:start + size]
        encoded = _normalized(embedder.encode([chunks[i].index_text for i in positions]))
        if encoded.shape[0] != len(positions):
            raise ValueError("embedder returned wrong number of vectors")
        if any(vec is not None and vec.shape != encoded[0].shape for vec in vectors):
            raise ValueError("cached vector dimensionality mismatch")
        for j, pos in enumerate(positions):
            vectors[pos] = encoded[j]
            if cache:
                cache.put(chunks[pos].chunk_id, chunks[pos].text_sha, encoded[j])
        if cache:
            cache.save()
    matrix = np.stack(vectors).astype(np.float32)
    return _normalized(matrix)


def embed_chunks(embedder: BaseEmbedder | None, chunks, use_cache: bool = True) -> np.ndarray | None:
    """Backward-compatible alias for incremental embedding."""
    return embed_missing(embedder, chunks, use_cache)


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
