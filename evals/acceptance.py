"""Weight-free, read-only-input workflow acceptance with an independent SciPy numerical check."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
from scipy.integrate import trapezoid
from scipy.signal import welch

from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.outputs import export, plot
from eegagent.worker.processing import extract_features, segment_signal
from eegagent.worker.readers import MNE_EXTS, file_ext, read_record, record_files, resolve_main_file
from eegagent.worker.store import load_artifact, resolve_inside_storage


STANDARD_BANDS = {"delta": [0.5, 4], "theta": [4, 8], "alpha": [8, 13], "beta": [13, 30], "gamma": [30, 50]}
WINDOW_S = 2.0
MAX_OBSERVED_S = 20.0
ARRAY_RTOL = 1e-5
ARRAY_ATOL = 1e-6


def file_sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def run_acceptance(input_path: Path, output_dir: Path, sample_rate_hz: float | None = None,
                   channel_names: list[str] | None = None, trial_index: int | None = None) -> dict:
    source = resolve_main_file(input_path.resolve())
    if not source.is_file():
        raise FileNotFoundError(source)
    output = output_dir.resolve()
    if source.is_relative_to(output) or (output.exists() and any(output.iterdir())):
        raise ValueError("Use an empty output directory outside the input record; existing results are never overwritten.")
    sources = record_files(source)
    before = {str(path): file_sha256(Path(path)) for path in sources}
    output.mkdir(parents=True, exist_ok=True)
    previous = {name: os.environ.get(name) for name in ("EEGAGENT_STORAGE_DIR", "EEGAGENT_DATA_DIRS")}
    os.environ.update(EEGAGENT_STORAGE_DIR=str(output), EEGAGENT_DATA_DIRS=str(source.parent))
    try:
        manifest = inspect_record(str(source), sample_rate_hz=sample_rate_hz,
                                  channel_names=channel_names, trial_index=trial_index)
        sr = manifest["sample_rate_hz"]
        if not sr or manifest["duration_s"] is None or manifest["duration_s"] < WINDOW_S:
            raise ValueError("A known sampling rate and at least two seconds of data are required.")
        end = min(MAX_OBSERVED_S, manifest["duration_s"])
        window_art = segment_signal(manifest["manifest_id"], WINDOW_S, WINDOW_S, partial="drop", start_s=0, end_s=end)["artifact"]
        feature_art = extract_features(window_art["artifact_id"], ["bandpower"], bands="standard")["artifact"]
        feature_record = load_artifact(feature_art["artifact_id"])
        summary = feature_record["summary"]
        windows = json.loads(resolve_inside_storage(window_art["uri"]).read_text(encoding="utf-8"))["windows"]
        raw = read_record(source, sample_rate_hz=sr, channel_names=manifest["channel_names"],
                          trial_index=trial_index, max_seconds=end)
        indices = [raw.channel_names.index(name) for name in summary["channel_names"]]
        data = np.array(raw.data[indices], dtype=float, copy=True)
        if file_ext(source) not in MNE_EXTS:
            data *= {"V": 1e6, "mV": 1e3}.get(manifest["unit"]["value"], 1.0)
        bands = {name: bounds for name, bounds in STANDARD_BANDS.items() if bounds[1] < sr / 2}
        reference = []
        for window in windows:
            segment = data[:, window["start_sample"]:window["end_sample"]]
            nperseg = min(int(max(64, 2 * sr)), segment.shape[1])
            frequency, density = welch(segment, fs=sr, window="hamming", nperseg=nperseg,
                                       noverlap=nperseg // 2, nfft=nperseg, detrend="constant",
                                       scaling="density", average="mean", axis=1)
            powers = []
            for low, high in bands.values():
                selected = (frequency >= low) & (frequency <= high)
                powers.append(trapezoid(density[:, selected], frequency[selected], axis=1))
            reference.append(np.stack(powers, axis=1))
        reference = np.asarray(reference)
        with np.load(resolve_inside_storage(feature_record["uri"])) as archive:
            measured = archive["bandpower"]
        shape_matches = measured.shape == reference.shape
        arrays_match = shape_matches and np.isfinite(measured).all() and np.allclose(
            measured, reference, rtol=ARRAY_RTOL, atol=ARRAY_ATOL)
        mean_power = reference.mean(axis=(0, 1))
        relative = mean_power / mean_power.sum() if mean_power.sum() else np.zeros_like(mean_power)
        summary_matches = all(
            np.isclose(summary["mean_bandpower"][name], mean_power[i], rtol=1e-3, atol=1e-4)
            and (summary["relative_bandpower"][name] is None if not mean_power.sum()
                 else np.isclose(summary["relative_bandpower"][name], relative[i], atol=5.1e-5))
            for i, name in enumerate(bands)
        )
        figure_art = plot("psd", manifest["manifest_id"], start_s=0, end_s=end,
                          channels=summary["channel_names"][:4])["artifact"]
        csv_art = export("csv", feature_art["artifact_id"])["artifact"]
        svg_art = export("svg", figure_art["artifact_id"])["artifact"]
        records = {art["artifact_id"]: load_artifact(art["artifact_id"])
                   for art in (window_art, feature_art, figure_art, csv_art, svg_art)}
        with resolve_inside_storage(csv_art["uri"]).open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        exported_values = []
        for row in rows:
            wi = int(row["window"])
            ci = summary["channel_names"].index(row["channel"])
            bi = list(bands).index(row["component"])
            exported_values.append(
                np.isclose(float(row["value"]), reference[wi, ci, bi], rtol=ARRAY_RTOL, atol=ARRAY_ATOL)
                and row["unit"] == summary["power_unit"]
            )
        after = {str(path): file_sha256(Path(path)) for path in sources}
        checks = {
            "input_unchanged": before == after,
            "declared_bands": summary["bands"] == bands,
            "bandpower_arrays": bool(arrays_match),
            "bandpower_summary": bool(summary_matches),
            "csv_values_and_units": len(rows) == reference.size and all(exported_values),
            "svg_readable": ElementTree.parse(resolve_inside_storage(svg_art["uri"])).getroot().tag.endswith("svg"),
            "artifact_files": all(resolve_inside_storage(record["uri"]).stat().st_size > 0 for record in records.values()),
            "source_lineage": all(manifest["manifest_id"] in record["source_manifest_ids"] for record in records.values()),
            "parent_lineage": all(parent in records for record in records.values()
                                  for parent in record.get("parent_artifact_ids", [])),
        }
        report = {
            "protocol": "weight_free_workflow_v1",
            "passed": all(checks.values()),
            "checks": checks,
            "input_sha256": before[str(source)],
            "source_format": manifest["source_format"],
            "sample_rate_hz": sr,
            "unit_evidence": manifest["unit"],
            "observed_range_s": [0, end],
            "window_s": WINDOW_S,
            "n_windows": len(windows),
            "n_eeg_channels": len(indices),
            "max_absolute_bandpower_error": float(np.max(np.abs(measured - reference))) if shape_matches else None,
            "reference_mean_bandpower": dict(zip(bands, mean_power.tolist())),
            "array_tolerance": {"rtol": ARRAY_RTOL, "atol": ARRAY_ATOL},
            "artifact_ids": list(records),
            "scope": "Deterministic measurement/export consistency under recorded metadata; not LLM task success, physical calibration or emotion-recognition accuracy.",
        }
        (output / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-rate", type=float)
    parser.add_argument("--channels", help="Comma-separated confirmed channel names, if absent from the input")
    parser.add_argument("--trial-index", type=int)
    args = parser.parse_args(argv)
    names = [name.strip() for name in args.channels.split(",")] if args.channels else None
    result = run_acceptance(args.input, args.out, args.sample_rate, names, args.trial_index)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
