"""Mixed Chinese/English tokenizer used by BM25 and alias matching."""

from __future__ import annotations

import re

CJK = re.compile(r"[一-鿿]+")
ASCII = re.compile(r"[a-z0-9]+(?:[.\-_][a-z0-9]+)*")
CJK_STOP = frozenset("的了是在有和与或把被让给对从向到于及等个种次这那之着过吗呢吧啊")


def normalize(text: str) -> str:
    return text.lower().replace("µ", "u").replace("μ", "u").replace("–", "-").replace("—", "-")


def tokenize(text: str) -> list[str]:
    t = normalize(text).replace("\\_", "_")
    toks = ASCII.findall(t)
    for seq in CJK.findall(t):
        toks.extend(ch for ch in seq if ch not in CJK_STOP)
        toks.extend(seq[i:i + 2] for i in range(len(seq) - 1))
        if len(seq) >= 3:
            toks.extend(seq[i:i + 3] for i in range(len(seq) - 2))
    return toks
