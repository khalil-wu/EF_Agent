"""Text decoding and upload classification for text-like files.

Chinese acquisition software commonly writes GBK/GB18030 while most tools write UTF-8; decoding never depends on the
host locale. Delimited text can be a signal table (OpenBCI exports, generic CSV) or a small table of labels, events or
notes; ``classify_text_table`` separates them so only signal tables become EEG records.
"""

from __future__ import annotations

import re
from pathlib import Path

TEXT_TABLE_EXTS = {".csv", ".tsv", ".txt"}
SIGNAL_METADATA_PREFIXES = ("# EFAgent metadata:", "# EEGAgent metadata:")
MIN_SIGNAL_ROWS = 256
_ANNOTATION_COLUMNS = re.compile(
    r"^(onset|duration|trial_?type|trial|event|events|marker|condition|stimulus|label|labels|rating|score|"
    r"valence|arousal|dominance|liking|familiarity|emotion|subject|participant|session|response|rt|"
    r"事件|标记|条件|刺激|标签|评分|效价|唤醒|优势度|喜好|情绪|被试|试次)$", re.I)


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decode bytes with universal newlines, like text-mode open()."""
    try:
        return _newlines(raw.decode("utf-8-sig")), "utf-8-sig"
    except UnicodeDecodeError:
        return _newlines(raw.decode("gb18030")), "gb18030"


def _newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path: str | Path, limit: int | None = None) -> tuple[str, str]:
    """Decode a whole file, or its first ``limit`` bytes cut back to the last complete line."""
    with Path(path).open("rb") as stream:
        raw = stream.read() if limit is None else stream.read(limit + 1)
    if limit is not None and len(raw) > limit:
        raw = raw[:limit]
        raw = raw[:raw.rfind(b"\n") + 1] or raw
    return decode_text(raw)


def _number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def delimiter_for(line: str) -> str | None:
    return "," if "," in line else ("\t" if "\t" in line else (";" if ";" in line else None))


def classify_text_table(path: str | Path, *, sample_rate_hz=None, channel_names=None) -> str:
    """Return "signal", "table" or "text" for a .csv/.tsv/.txt file."""
    text, _ = read_text(path, limit=2_000_000)
    lines = [line.strip() for line in text.splitlines()]
    declared_signal = sample_rate_hz is not None or bool(channel_names) or any(line.startswith(SIGNAL_METADATA_PREFIXES) for line in lines)
    body = [line for line in lines if line and not line.startswith(("%", "#"))]
    if not body:
        return "text"
    delimiter = delimiter_for(body[0])
    split = (lambda line: [token.strip().strip('"') for token in line.split(delimiter)]) if delimiter else str.split
    header = split(body[0])
    has_header = any(token and not _number(token) for token in header)
    rows = body[1:] if has_header else body
    sample = rows[:200]
    numeric_rows = sum(1 for line in sample if (fields := split(line)) and
                       sum(_number(field) for field in fields) >= max(1, len(fields) // 2))
    if not sample or numeric_rows < 0.8 * len(sample):
        return "text"
    annotations = sum(bool(_ANNOTATION_COLUMNS.match(token)) for token in header) if has_header else 0
    if annotations and len(header) - annotations < 2:
        return "table"
    return "signal" if len(rows) >= MIN_SIGNAL_ROWS or declared_signal else "table"
