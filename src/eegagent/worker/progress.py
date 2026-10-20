"""Public execution stages, scoped to the currently executing tool."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

_sink = ContextVar("tool_progress", default=lambda event: None)


def report(stage: str, label: str, detail: str = "") -> None:
    _sink.get()({"stage": stage, "label": label, "detail": detail,
                 "ts": datetime.now(timezone.utc).isoformat()})


def forward(event: dict) -> None:
    """Deliver a real stage emitted by a script subprocess to the current tool channel."""
    _sink.get()(event)


@contextmanager
def channel(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
