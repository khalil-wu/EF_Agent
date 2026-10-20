from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class WindowPolicy:
    window_s: float
    stride_s: float
    partial: str = "drop"
    max_windows: int | None = None


@dataclass(frozen=True)
class SignalWindow:
    index: int
    start_s: float
    end_s: float
    pad_left_s: float = 0.0
    pad_right_s: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s + self.pad_left_s + self.pad_right_s


def _uniform_indices(size: int, limit: int) -> list[int]:
    if limit <= 0 or size <= limit:
        return list(range(size))
    if limit == 1:
        return [0]
    return [round(i * (size - 1) / (limit - 1)) for i in range(limit)]


def plan_windows(duration_s: float, policy: WindowPolicy) -> list[SignalWindow]:
    """Plan deterministic windows for a signal or a video timeline."""
    if duration_s <= 0:
        return []

    full_count = floor((duration_s - policy.window_s) / policy.stride_s) + 1
    full_count = max(full_count, 0)
    windows: list[SignalWindow] = []

    for i in range(full_count):
        start = i * policy.stride_s
        windows.append(SignalWindow(i, start, start + policy.window_s))

    covered_until = windows[-1].end_s if windows else 0.0
    if policy.partial == "pad" and covered_until < duration_s:
        start = max(0.0, duration_s - policy.window_s)
        end = min(duration_s, start + policy.window_s)
        if not windows or start > windows[-1].start_s:
            windows.append(
                SignalWindow(
                    len(windows),
                    start,
                    end,
                    pad_left_s=0.0,
                    pad_right_s=max(0.0, policy.window_s - (end - start)),
                )
            )
    elif policy.partial == "keep" and covered_until < duration_s:
        start = covered_until
        windows.append(SignalWindow(len(windows), start, duration_s))

    indices = _uniform_indices(len(windows), policy.max_windows or 0)
    return [
        SignalWindow(i, windows[j].start_s, windows[j].end_s, windows[j].pad_left_s, windows[j].pad_right_s)
        for i, j in enumerate(indices)
    ]
