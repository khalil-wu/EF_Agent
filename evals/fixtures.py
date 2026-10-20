"""Synthetic fixtures for the evaluation set. Deterministic (seeded), µV scale unless stated.

Each generator writes a file into ``out_dir`` and returns (path, upload_form_fields, ground_truth).
Ground truth describes injected signal properties only. None of these fixtures has emotion labels.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _base(rng, n_ch, sr, secs, noise=5.0):
    t = np.arange(int(sr * secs)) / sr
    return t, rng.normal(0, noise, (n_ch, len(t)))


def homebrew_8ch(out: Path):
    """8 channels, 250 Hz, no channel names, 60 s, alpha burst 20–40 s on channels 6/7."""
    rng = np.random.default_rng(11)
    sr = 250
    t, x = _base(rng, 8, sr, 60)
    burst = (t >= 20) & (t < 40)
    x[6] += 30 * np.sin(2 * np.pi * 10 * t) * burst
    x[7] += 30 * np.sin(2 * np.pi * 10 * t) * burst
    p = out / "homebrew_8ch.npy"
    np.save(p, x)
    return p, {"sample_rate_hz": "250"}, {"n_channels": 8, "sr": sr, "layout_known": False, "alpha_peak_window": (20, 40), "alpha_channels": [6, 7]}


def named_8ch_openbci(out: Path):
    """Same as homebrew but with OpenBCI Cyton default names supplied at upload."""
    p, form, gt = homebrew_8ch(out)
    p2 = out / "openbci_8ch.npy"
    p2.write_bytes(p.read_bytes())
    form = dict(form, channel_names="Fp1,Fp2,C3,C4,P7,P8,O1,O2")
    gt = dict(gt, layout_known=True, alpha_channel_names=["O1", "O2"])
    return p2, form, gt


def single_channel_forehead(out: Path):
    """1 channel Fp1, 512 Hz, 30 s, strong eye-blink-like slow waves."""
    rng = np.random.default_rng(12)
    sr = 512
    t, x = _base(rng, 1, sr, 30)
    for c in np.arange(2, 30, 3.0):
        x[0] += 120 * np.exp(-((t - c) ** 2) / (2 * 0.08 ** 2))
    p = out / "forehead_1ch.csv"
    with p.open("w", encoding="utf-8") as f:
        f.write("time,Fp1\n")
        for i in range(len(t)):
            f.write(f"{t[i]:.6f},{x[0, i]:.3f}\n")
    return p, {"sample_rate_hz": str(sr)}, {"n_channels": 1, "sr": sr, "layout_known": True}


def volts_scaled(out: Path):
    """32 named channels but stored in Volts (like a BDF export): unit must be inferred as V."""
    rng = np.random.default_rng(13)
    sr = 128
    names = ["FP1", "AF3", "F3", "F7", "FC5", "FC1", "C3", "T7", "CP5", "CP1", "P3", "P7", "PO3", "O1", "OZ", "PZ",
             "FP2", "AF4", "FZ", "F4", "F8", "FC6", "FC2", "CZ", "C4", "T8", "CP6", "CP2", "P4", "P8", "PO4", "O2"]
    t, x = _base(rng, 32, sr, 60, noise=8.0)
    x[13] += 20 * np.sin(2 * np.pi * 10 * t)
    x[31] += 20 * np.sin(2 * np.pi * 10 * t)
    p = out / "deap_like_volts.npy"
    np.save(p, x * 1e-6)
    return p, {"sample_rate_hz": "128", "channel_names": ",".join(names)}, {"n_channels": 32, "sr": sr, "layout_known": True, "unit": "V"}


def line_noise_heavy(out: Path):
    """8 named channels, 250 Hz, huge 50 Hz on two channels and one flat channel."""
    rng = np.random.default_rng(14)
    sr = 250
    t, x = _base(rng, 8, sr, 40)
    x[0] += 60 * np.sin(2 * np.pi * 50 * t)
    x[3] += 60 * np.sin(2 * np.pi * 50 * t)
    x[5] = 0.0
    p = out / "noisy_8ch.npy"
    np.save(p, x)
    return p, {"sample_rate_hz": "250", "channel_names": "Fp1,Fp2,C3,C4,P7,P8,O1,O2"}, {"flat_channels": ["P8"], "line_noise_channels": ["Fp1", "C4"], "sr": sr}


FIXTURES = {
    "homebrew_8ch": homebrew_8ch,
    "openbci_8ch": named_8ch_openbci,
    "forehead_1ch": single_channel_forehead,
    "volts_32ch": volts_scaled,
    "noisy_8ch": line_noise_heavy,
}
