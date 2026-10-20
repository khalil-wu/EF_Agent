from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SignalEvent:
    onset_s: float
    duration_s: float
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalManifest:
    source_path: str
    source_format: str
    sample_rate_hz: float
    channel_names: tuple[str, ...]
    n_samples: int
    unit: str = "uV"
    dataset: str | None = None
    montage: str | None = None
    events: tuple[SignalEvent, ...] = ()

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.sample_rate_hz

    @property
    def n_channels(self) -> int:
        return len(self.channel_names)
