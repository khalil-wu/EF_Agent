from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import store
from eegagent.worker.cleaning import clean_artifacts
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.processing import extract_features, segment_signal
from eeg_synth import NAMES19, make_eeg, save_set


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    from eegagent.worker import signals

    signals._CACHE.clear()
    return store.ensure_layout()


@pytest.fixture()
def blink_set(storage: Path) -> tuple[dict, np.ndarray]:
    data, sr, names, blink = make_eeg(sr=128, secs=90)
    stim = np.zeros(data.shape[1])
    stim[::640] = 1
    p = save_set(storage / "uploads" / "blinks.set", data, sr, names, extra={"EOG1": ("eog", blink), "STATUS": ("stim", stim)})
    return inspect_record(str(p)), data


def _rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def test_ica_iclabel_removes_blink_component(blink_set):
    from eegagent.worker.progress import channel
    m, data = blink_set
    stages = []
    with channel(stages.append):
        out = clean_artifacts(m["manifest_id"], "ica_iclabel")
    s = out["summary"]
    assert any(r["label"] == "eye blink" for r in s["removed"])
    assert s["n_channels_ica"] == 19 and s["n_components"] == 18 and 0 < s["removed_variance_ratio"] < 1
    art = out["artifact"]
    assert art["kind"] == "normalized_signal" and art["provenance"]["steps"][-2:] == ["rereference_car", f"ica_iclabel_removed_{len(s['removed'])}"]
    cleaned = np.load(art["uri"])
    fp1 = NAMES19.index("Fp1")
    assert _rms(cleaned[fp1]) < 0.6 * _rms(data[fp1] - data[:19].mean(axis=0))  # blink energy is gone from Fp1
    from eegagent.worker.signals import load_signal

    orig = load_signal(m["manifest_id"])[0]
    assert np.allclose(cleaned[19], orig[19], atol=1e-3) and np.abs(cleaned[20]).max() == 1.0  # EOG / trigger untouched
    assert Path(s["ica_solution_path"]).exists()
    assert out["artifacts"][0]["kind"] == "plot" and Path(out["artifacts"][0]["uri"]).exists()
    figure = out["artifacts"][0]
    assert figure["summary"]["title"] == "ICA 成分头皮分布"
    assert "空间权重" in figure["summary"]["description"]
    assert {item["index"] for item in figure["summary"]["components"]} == set(figure["summary"]["components_shown"])
    assert all(item["removed"] == (item["index"] in {r["index"] for r in s["removed"]})
               for item in figure["summary"]["components"])
    assert figure["analysis"]["aggregation"] == "independent component spatial weights over standard electrode positions"
    assert [item["stage"] for item in stages] == ["clean_load", "ica_prepare", "ica_filter", "ica_fit", "ica_classify",
                                                   "ica_apply", "ica_figure", "clean_save_signal", "clean_save_figure"]
    again = clean_artifacts(m["manifest_id"], "ica_iclabel")
    assert again.get("cached") and again["artifact"]["artifact_id"] == art["artifact_id"] and again["artifacts"]


def test_asr_attenuates_bursts_and_chains_into_features(blink_set):
    m, data = blink_set
    out = clean_artifacts(m["manifest_id"], "asr", cutoff=10)
    s = out["summary"]
    assert out["artifact"]["provenance"]["steps"][-2:] == ["highpass_0.5", "asr_k10"]
    assert 0 < s["changed_fraction"] < 1 and s["most_changed_seconds"]
    assert out["artifacts"][0]["summary"]["title"] == "ASR 清洗前后对照"
    assert "重构变化较大" in out["artifacts"][0]["summary"]["description"]
    cleaned = np.load(out["artifact"]["uri"])
    fp1 = NAMES19.index("Fp1")
    assert _rms(cleaned[fp1]) < 0.8 * _rms(data[fp1] - data[fp1].mean())
    win = segment_signal(out["artifact"]["artifact_id"], window_s=4)["artifact"]["artifact_id"]
    feats = extract_features(win, ["bandpower"])
    assert feats["summary"]["n_windows"] == 22


def test_bad_channels_detects_flat_and_noisy(storage: Path):
    data, sr, names, _ = make_eeg(sr=128, secs=60, blinks=False)
    data[names.index("T8")] = 0.0
    data[names.index("P3")] += np.random.default_rng(5).normal(0, 60, data.shape[1])
    m = inspect_record(str(save_set(storage / "uploads" / "bad.set", data, sr, names)))
    out = clean_artifacts(m["manifest_id"], "bad_channels")
    s = out["summary"]
    assert {"T8", "P3"} <= set(s["bad_channels"]) and "T8" in s["by_criterion"]["bad_by_flat"]
    if s["action"] == "interpolate":
        fixed = np.load(out["artifact"]["uri"])
        assert fixed.shape[0] == 19 and fixed[names.index("T8")].std() > 1.0
    dropped = clean_artifacts(m["manifest_id"], "bad_channels", action="drop")
    assert dropped["artifact"]["shape"][0] == 19 - len(dropped["summary"]["bad_channels"])


def test_infeasible_requests_explain_alternatives(storage: Path):
    rng = np.random.default_rng(0)
    four = storage / "uploads" / "muse.npy"
    np.save(four, rng.normal(0, 20, (4, 256 * 60)))
    m = inspect_record(str(four), sample_rate_hz=256, channel_names=["TP9", "AF7", "AF8", "TP10"])
    with pytest.raises(ValueError, match="asr"):
        clean_artifacts(m["manifest_id"], "ica_iclabel")
    unnamed = storage / "uploads" / "unnamed.npy"
    np.save(unnamed, rng.normal(0, 20, (19, 128 * 60)))
    m2 = inspect_record(str(unnamed), sample_rate_hz=128)
    with pytest.raises(ValueError, match="device_lookup"):
        clean_artifacts(m2["manifest_id"], "ica_iclabel")
    with pytest.raises(ValueError):
        clean_artifacts(m2["manifest_id"], "wavelet")
