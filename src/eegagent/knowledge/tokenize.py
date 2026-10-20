"""Mixed Chinese/English tokenizer used by BM25 and alias matching."""

from __future__ import annotations

import re

CJK = re.compile(r"[一-鿿]+")
ASCII = re.compile(r"[a-z0-9]+(?:[.\-_][a-z0-9]+)*")


def normalize(text: str) -> str:
    return text.lower().replace("µ", "u").replace("μ", "u").replace("–", "-").replace("—", "-")


def tokenize(text: str) -> list[str]:
    t = normalize(text).replace("\\_", "_")
    toks = ASCII.findall(t)
    for seq in CJK.findall(t):
        toks.extend(seq)
        toks.extend(seq[i:i + 2] for i in range(len(seq) - 1))
    return toks
