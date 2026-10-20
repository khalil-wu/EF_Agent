import io
import json
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import signals, store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.processing import extract_features, preprocess, segment_signal
from evals.outcomes import classification_scores, interval_scores, sleep_scores
from evals.reference import bandpower_reference


def test_outcomes_penalize_wrong_labels_duplicate_events_and_boundaries():
    scores = classification_scores(["clean", "ocular", "muscle"], ["clean", "clean", "muscle"], ["clean", "ocular", "muscle"])
    assert scores["accuracy"] == pytest.approx(2 / 3)
    assert scores["macro_f1"] == pytest.approx(5 / 9)
    duplicate = interval_scores([[20, 40]], [[20, 40], [20, 40]])
    assert duplicate["true_positive"] == 1 and duplicate["false_positive"] == 1
    assert duplicate["interval_iou_f1"] == pytest.approx(2 / 3)
    shifted = interval_scores([[20, 40]], [[18, 42]])
    assert shifted["max_boundary_error_s"] == 2
    assert interval_scores([], []) ["interval_iou_f1"] == 1
    labels = ["W", "N1", "N2", "N3", "REM"]
    correct = sleep_scores(labels, labels)
    assert correct["macro_f1"] == correct["cohen_kappa"] == 1
    with pytest.raises(ValueError, match="数量"):
        sleep_scores(labels, labels[:2])


@pytest.mark.parametrize("processed", [False, True])
def test_independent_reference_rejects_wrong_values_even_when_ratios_sum_to_one(tmp_path, monkeypatch, processed):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    path = store.ensure_layout() / "uploads" / "reference.npy"
    time = np.arange(2000) / 250
    np.save(path, (5 * np.sin(2 * np.pi * 10 * time))[None, :])
    manifest = inspect_record(str(path), sample_rate_hz=250, channel_names=["O1"])
    source = (preprocess(manifest["manifest_id"], [{"op": "bandpass", "low": 1, "high": 45}])["artifact"]["artifact_id"]
              if processed else manifest["manifest_id"])
    windows = segment_signal(source, 2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]

    class Response:
        def __init__(self, value=None, content=None):
            self.value, self.content = value, content

        def json(self):
            return self.value

        def raise_for_status(self):
            pass

    def get(url, timeout):
        route = url.removeprefix("http://reference")
        if route.startswith("/sessions/"):
            return Response({"manifests": [manifest]})
        artifact_id = route.split("/")[2]
        record = store.load_artifact(artifact_id)
        if route.endswith("/file"):
            content = Path(record["uri"]).read_bytes()
            return Response(json.loads(content) if record["media_type"] == "application/json" else None, content)
        return Response(record)

    monkeypatch.setattr("evals.reference.requests.get", get)
    assert bandpower_reference("http://reference", features, path, "ses_test")["passed"]
    record = store.load_artifact(features)
    original_relative = record["summary"]["relative_bandpower"].copy()
    record["summary"]["relative_bandpower"] = {band: (.7 if band == "alpha" else .3 if band == "beta" else 0)
                                                 for band in record["summary"]["bands"]}
    store.save_artifact(record)
    assert not bandpower_reference("http://reference", features, path, "ses_test")["passed"]
    record["summary"]["relative_bandpower"] = original_relative
    store.save_artifact(record)
    with np.load(record["uri"]) as arrays:
        altered = {name: arrays[name].copy() for name in arrays.files}
    altered["bandpower"] *= 2
    np.savez_compressed(record["uri"], **altered)
    assert not bandpower_reference("http://reference", features, path, "ses_test")["passed"]
