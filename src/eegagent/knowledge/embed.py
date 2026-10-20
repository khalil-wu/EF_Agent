"""Pluggable embedding / reranking providers with a disk cache.

Env:
  EEGAGENT_EMBED_PROVIDER   none | sentence_transformers | openai   (auto-detected if unset)
  EEGAGENT_EMBED_MODEL      local path or hub name (sentence_transformers) / model id (openai)
  EEGAGENT_EMBED_BASE_URL   OpenAI-compatible base url, e.g. https://api.example.com/v1
  EEGAGENT_EMBED_API_KEY
  EEGAGENT_RERANK_MODEL     cross-encoder path/name (sentence_transformers), optional
  EEGAGENT_STORAGE_DIR      cache lives in <storage>/knowledge_index/
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np


class BaseEmbedder:
    name = "none"
    dim = 0

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError


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
    def __init__(self, base_url: str, api_key: str, model: str, batch: int = 64):
        import requests  # optional dependency

        self._rq = requests
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model
        self.name = "openai:" + model
        self.batch = batch
        self.dim = 0

    def encode(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i:i + self.batch]
            r = self._rq.post(f"{self.base_url}/embeddings", json={"model": self.model_name, "input": chunk},
                              headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}, timeout=120)
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            out.extend(d["embedding"] for d in data)
        v = np.asarray(out, dtype=np.float32)
        if v.ndim == 2 and v.size:
            v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
            self.dim = v.shape[1]
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
            return OpenAIEmbedder(base, os.environ.get("EEGAGENT_EMBED_API_KEY", ""), model)
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


def make_reranker() -> Reranker | None:
    model = os.environ.get("EEGAGENT_RERANK_MODEL", "").strip()
    if not model:
        return None
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


class VectorCache:
    """chunk_id -> (text_sha, vector), persisted per embedder as npz + json."""

    def __init__(self, embedder_name: str):
        slug = re.sub(r"[^a-z0-9]+", "_", embedder_name.lower()).strip("_") or "none"
        self.base = _cache_dir() / f"emb_{slug}"
        self.meta: dict[str, str] = {}
        self.vectors: dict[str, np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        mp, vp = self.base.with_suffix(".json"), self.base.with_suffix(".npz")
        if mp.exists() and vp.exists():
            try:
                self.meta = json.loads(mp.read_text(encoding="utf-8"))
                z = np.load(vp)
                self.vectors = {k: z[k] for k in z.files}
            except Exception:  # noqa: BLE001
                self.meta, self.vectors = {}, {}

    def save(self) -> None:
        self.base.with_suffix(".json").write_text(json.dumps(self.meta), encoding="utf-8")
        if self.vectors:
            np.savez_compressed(self.base.with_suffix(".npz"), **self.vectors)

    def get(self, chunk_id: str, text_sha: str) -> np.ndarray | None:
        if self.meta.get(chunk_id) == text_sha:
            return self.vectors.get(chunk_id)
        return None

    def put(self, chunk_id: str, text_sha: str, vec: np.ndarray) -> None:
        self.meta[chunk_id] = text_sha
        self.vectors[chunk_id] = np.asarray(vec, dtype=np.float32)


def embed_chunks(embedder: BaseEmbedder | None, chunks, use_cache: bool = True) -> np.ndarray | None:
    """Return a [n, dim] matrix aligned with chunks, encoding only cache misses. None if no embedder."""
    if embedder is None or not chunks:
        return None
    cache = VectorCache(embedder.name) if use_cache else None
    vecs: list[np.ndarray | None] = []
    missing: list[int] = []
    for i, c in enumerate(chunks):
        v = cache.get(c.chunk_id, c.text_sha) if cache else None
        vecs.append(v)
        if v is None:
            missing.append(i)
    if missing:
        enc = embedder.encode([chunks[i].index_text for i in missing])
        for j, i in enumerate(missing):
            vecs[i] = enc[j]
            if cache:
                cache.put(chunks[i].chunk_id, chunks[i].text_sha, enc[j])
        if cache:
            cache.save()
    dim = len(next(v for v in vecs if v is not None))
    return np.stack([v if v is not None else np.zeros(dim, dtype=np.float32) for v in vecs])


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
