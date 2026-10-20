from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import signals, store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.measurements import detect_transients
from eegagent.worker.outputs import export, read_artifact
from eegagent.worker.tools import run_tool


@pytest.fixture()
def signal_record(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    path = store.ensure_layout() / "uploads" / "measurement.npy"
    sr = 250
    time = np.arange(sr * 20) / sr
    signal = 120 * np.exp(-((time - 15) / .03) ** 2 / 2)
    np.save(path, np.vstack([signal, np.zeros_like(signal)]))
    return inspect_record(str(path), sample_rate_hz=sr, channel_names=["Fp1", "O1"])


def test_full_quality_includes_tail_and_per_channel_metrics(signal_record):
    first = signal_record["quality"]["amplitude_p99"]
    result = run_tool("inspect_record", {"manifest_id": signal_record["manifest_id"], "quality_scope": "full"})["manifest"]
    quality = result["quality"]
    assert quality["sample_time_range_s"] == [0, 20]
    assert quality["flat_channels"] == ["O1"]
    assert quality["channels"][0]["peak_to_peak"] == pytest.approx(120)
    assert quality["channels"][0]["rms"] > 0 and first == 0
    assert quality["channels"][1]["flat_line_ratio"] == 1
    assert quality["channels"][1]["line_noise_ratio"] is None


def test_transient_candidate_preserves_time_and_can_be_paged(signal_record):
    result = detect_transients(signal_record["manifest_id"], start_s=12, end_s=18, channels=["Fp1"])
    assert result["summary"]["rows"] == 1
    row = read_artifact(result["artifact"]["artifact_id"], part="rows")["rows"][0]
    assert row["peak_s"] == pytest.approx(15)
    assert row["amplitude"] == pytest.approx(120)
    assert row["width_s"] == pytest.approx(2.355 * .03, abs=.004)
    assert row["start_s"] < 15 < row["end_s"]


def test_transient_candidates_export_csv_and_json_with_coordinates(signal_record):
    result = detect_transients(signal_record["manifest_id"], start_s=12, end_s=18, channels=["Fp1"])
    source = result["artifact"]["artifact_id"]
    csv_result = export("csv", source)["artifact"]
    page = read_artifact(csv_result["artifact_id"], part="rows")
    assert page["total"] == 1 and page["rows"][0]["channel"] == "Fp1"
    assert float(page["rows"][0]["peak_s"]) == pytest.approx(15)
    json_result = export("json", source)["artifact"]
    json_page = read_artifact(json_result["artifact_id"], part="rows")
    assert json_page["rows"] == result["rows"]
    assert json_result["source_manifest_ids"] == [signal_record["manifest_id"]]
    assert json_result["parent_artifact_ids"] == [source]
