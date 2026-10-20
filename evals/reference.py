"""Numerical references use source signals and SciPy, not the worker feature implementation."""

import csv
import io

import numpy as np
import requests
from scipy.signal import welch
from scipy.integrate import trapezoid

from eegagent.worker.readers import read_record
from eegagent.worker.channels import VOLT_TYPES
from evals.outcomes import interval_scores


def _get(gateway, path):
    response = requests.get(gateway + path, timeout=30)
    response.raise_for_status()
    return response


def bandpower_reference(gateway, artifact_id, raw_path, session_id):
    record = _get(gateway, f"/artifacts/{artifact_id}").json()
    window_id = record["parent_artifact_ids"][0]
    payload = _get(gateway, f"/artifacts/{window_id}/file").json()
    source = payload["source"]
    sr = payload["sample_rate_hz"]
    summary = record["summary"]
    if source.startswith("art_"):
        source_record = _get(gateway, f"/artifacts/{source}").json()
        data = np.load(io.BytesIO(_get(gateway, f"/artifacts/{source}/file").content))
        names = source_record["signal"]["channel_names"]
    else:
        session = _get(gateway, f"/sessions/{session_id}").json()
        manifest = next(m for m in session["manifests"] if m["manifest_id"] == source)
        raw = read_record(raw_path, sample_rate_hz=sr, channel_names=manifest["channel_names"])
        factor = {"V": 1e6, "mV": 1e3, "uV": 1, "unknown": 1}[manifest["unit"]["value"]]
        data = raw.data * np.array([factor if kind in VOLT_TYPES else 1 for kind in manifest["channel_types"]])[:, None]
        names = manifest["channel_names"]
    indices = [names.index(name) for name in summary["channel_names"]]
    data = data[indices]
    band_names = summary.get("band_names", list(summary["bands"]))
    nper = round(sr / summary["psd_resolution_hz"])
    reference = []
    for window in payload["windows"]:
        segment = data[:, window["start_sample"]:window["end_sample"]]
        frequencies, psd = welch(segment, fs=sr, nperseg=nper, window="hamming", axis=1)
        powers = []
        for band in band_names:
            low, high = summary["bands"][band]
            selected = (frequencies >= low) & (frequencies <= high)
            powers.append(trapezoid(psd[:, selected], frequencies[selected], axis=1))
        reference.append(np.stack(powers, axis=1))
    reference = np.array(reference)
    measured = np.load(io.BytesIO(_get(gateway, f"/artifacts/{artifact_id}/file").content))["bandpower"]
    means = reference.mean(axis=(0, 1))
    relative = means / means.sum()
    absolute_expected = dict(zip(band_names, means.tolist()))
    relative_expected = dict(zip(band_names, relative.tolist()))
    summary_passed = all(np.isclose(summary["mean_bandpower"][band], absolute_expected[band], rtol=1e-3, atol=1e-4)
                         and np.isclose(summary["relative_bandpower"][band], relative_expected[band], atol=5.1e-5)
                         for band in band_names)
    return {"artifact_id": artifact_id, "passed": bool(np.allclose(measured, reference, rtol=1e-5, atol=1e-6) and summary_passed),
            "max_absolute_error": float(np.max(np.abs(measured - reference))),
            "absolute_expected": absolute_expected, "relative_expected": relative_expected,
            "n_windows": len(payload["windows"]), "n_channels": len(indices)}


def interval_table_reference(gateway, events, expected, tolerance_s=1):
    tables = [event["result"] for event in events if event["type"] == "artifact_ready" and
              isinstance(event.get("result"), dict) and event["result"].get("kind") == "table"]
    prediction = None
    for table in tables:
        response = _get(gateway, f"/artifacts/{table['artifact_id']}/file")
        if table["media_type"] == "text/csv":
            reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
            if set(reader.fieldnames or []) == {"start_s", "end_s"}:
                prediction = [[float(row["start_s"]), float(row["end_s"])] for row in reader]
    if prediction is None:
        return {"passed": False, "reason": "没有导出约定的 start_s/end_s 区间表"}
    result = interval_scores(expected, prediction, min_iou=.8)
    result["passed"] = (result["false_positive"] == 0 and result["false_negative"] == 0 and
                        (result["max_boundary_error_s"] is None or result["max_boundary_error_s"] <= tolerance_s))
    result["prediction"] = prediction
    return result
