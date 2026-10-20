"""Synthetic EEG for tests: spatially smooth sources on the 10-20 template (neighbours correlate like real scalp EEG),
an occipital alpha source, and frontal eye blinks. Deterministic."""

from __future__ import annotations

import numpy as np

NAMES19 = ["Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "T7", "C3", "Cz", "C4", "T8", "P7", "P3", "Pz", "P4", "P8", "O1", "O2"]


def _pink(rng, n):
    f = np.fft.rfftfreq(n, 1.0)
    f[0] = f[1]
    x = np.fft.irfft((rng.normal(size=f.size) + 1j * rng.normal(size=f.size)) / np.sqrt(f), n)
    return x / x.std()


def _positions(names):
    import mne

    pos = mne.channels.make_standard_montage("standard_1020").get_positions()["ch_pos"]
    return np.array([pos[n] for n in names])


def make_eeg(sr: int = 128, secs: int = 90, seed: int = 0, blinks: bool = True, names=NAMES19):
    """Returns (data µV [19, N], sr, names, blink_source)."""
    rng = np.random.default_rng(seed)
    n = sr * secs
    t = np.arange(n) / sr
    xyz = _positions(names)
    n_src = 10
    centers = xyz[rng.choice(len(names), n_src, replace=False)] * 1.05
    d2 = ((xyz[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
    mix = np.exp(-d2 / (2 * 0.045 ** 2)) * 10.0
    src = np.stack([_pink(rng, n) for _ in range(n_src)])
    occ = xyz[names.index("Oz")] if "Oz" in names else (xyz[names.index("O1")] + xyz[names.index("O2")]) / 2
    alpha_mix = np.exp(-((xyz - occ) ** 2).sum(-1) / (2 * 0.06 ** 2)) * 14.0
    alpha = np.sin(2 * np.pi * 10 * t) * (1 + 0.5 * np.sin(2 * np.pi * 0.07 * t))
    data = mix @ src + alpha_mix[:, None] * alpha[None, :] + rng.normal(0, 1.5, (len(names), n))
    blink = np.zeros(n)
    if blinks:
        for c in np.arange(2.0, secs - 1, 3.7):
            i = int(c * sr)
            w = np.arange(-int(0.25 * sr), int(0.25 * sr))
            blink[i + w] += 160 * np.exp(-0.5 * (w / (0.07 * sr)) ** 2)
        fp = (xyz[names.index("Fp1")] + xyz[names.index("Fp2")]) / 2 + np.array([0, 0.02, -0.02])
        w = np.exp(-((xyz - fp) ** 2).sum(-1) / (2 * 0.05 ** 2))
        data = data + w[:, None] * blink[None, :]
    return data, sr, list(names), blink


def save_set(path, data_uv, sr, names, extra: dict | None = None, annotations=None):
    """Write an EEGLAB .set (via mne.export) with optional extra channels {name: (type, µV row)}."""
    import mne

    rows, chn, typ = [data_uv * 1e-6], list(names), ["eeg"] * len(names)
    for name, (ctype, row) in (extra or {}).items():
        rows.append(np.asarray(row, float)[None, :] * (1e-6 if ctype in ("eog", "ecg", "emg") else 1.0))
        chn.append(name)
        typ.append(ctype)
    raw = mne.io.RawArray(np.vstack(rows), mne.create_info(chn, sr, typ), verbose="ERROR")
    if annotations:
        raw.set_annotations(mne.Annotations(*zip(*annotations)))
    mne.export.export_raw(str(path), raw, fmt="eeglab", overwrite=True, verbose="ERROR")
    return path
