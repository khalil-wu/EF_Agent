"""BM25 (Okapi) over pre-tokenized documents."""

from __future__ import annotations

import math
from collections import Counter


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.n) if self.n else 1.0
        self.tf = [Counter(d) for d in docs]
        self.dl = [len(d) for d in docs]
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, q: list[str], i: int) -> float:
        s = 0.0
        tf, dl = self.tf[i], self.dl[i]
        for t in q:
            if t not in tf:
                continue
            f = tf[t]
            s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s

    def top(self, q: list[str], n: int) -> list[tuple[int, float]]:
        scored = [(i, self.score(q, i)) for i in range(self.n)]
        scored = [(i, s) for i, s in scored if s > 0]
        scored.sort(key=lambda x: -x[1])
        return scored[:n]
