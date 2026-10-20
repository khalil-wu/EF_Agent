import json
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import store
from eegagent.worker.inspect_record import infer_layout, infer_unit, inspect_record
from eegagent.worker.registry import check_model_compat


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    return store.ensure_layout()


def test_unit_inference_distinguishes_volts_and_microvolts():
    rng = np.random.default_rng(0)
    assert infer_unit(rng.normal(0, 30, (4, 1000))).get("value") == "uV"
    assert infer_unit(rng.normal(0, 30e-6, (4, 1000))).get("value") == "V"


def test_layout_known_only_when_most_names_are_ten_twenty():
    assert infer_layout(["Fp1", "Fp2", "C3", "C4"])["source"] == "file_header"
    assert infer_layout(["ch1", "ch2", "ch3", "ch4"])["source"] == "unknown"


def test_npy_unknown_device_gets_incompatible_models(storage: Path):
    rng = np.random.default_rng(1)
    arr = rng.normal(0, 20, (8, 250 * 30))  # 8 channels, 30 s at 250 Hz, µV scale
    path = storage / "uploads" / "homebrew.npy"
    np.save(path, arr)

    m = inspect_record(str(path), sample_rate_hz=250)
    assert m["n_channels"] == 8 and m["sample_rate_hz"] == 250 and m["duration_s"] == 30.0
    assert m['quality']['sample_original_time_ranges_s'][0]['original_time_range_s'] == [0., 10.]
    assert m['quality']['scope'] == 'initial_sample' and m['n_samples'] == 7500
    assert m['quality']['line_noise_original_time_ranges_s'][0]['original_time_range_s'] == [0., 4.]
    assert m["channel_layout"]["source"] == "unknown"
    assert m["unit"]["value"] == "uV"
    assert any("通道名" in w for w in m["warnings"])
    statuses = {c["model_id"]: c["status"] for c in m["compat"]}
    assert statuses  # registry loaded
    assert all(s == "incompatible" for s in statuses.values())
    saved = json.loads((storage / "manifests" / f"{m['manifest_id']}.json").read_text(encoding="utf-8"))
    assert saved["attachment_sha256"] == m["attachment_sha256"]


def test_csv_with_header_and_time_column(storage: Path):
    sr = 128
    t = np.arange(0, 5, 1 / sr)
    data = np.random.default_rng(2).normal(0, 10, (len(t), 3))
    path = storage / "uploads" / "rec.csv"
    with path.open("w", encoding="utf-8") as f:
        f.write("time,Fp1,Fp2,Cz\n")
        for i in range(len(t)):
            f.write(f"{t[i]:.6f},{data[i,0]:.3f},{data[i,1]:.3f},{data[i,2]:.3f}\n")
    m = inspect_record(str(path))
    assert m["channel_names"] == ["Fp1", "Fp2", "Cz"]
    assert abs(m["sample_rate_hz"] - sr) < 0.01
    assert m["channel_layout"]["source"] == "file_header"
    assert m["quality"]["sample_count"] == len(t)
    assert m["quality"]["sample_time_range_s"] == pytest.approx([0, 5], abs=0.001)
    assert m["quality"]["line_noise_time_range_s"] == pytest.approx([0, 4], abs=0.001)


def test_path_outside_storage_is_rejected(storage: Path, tmp_path_factory: pytest.TempPathFactory):
    outside = tmp_path_factory.mktemp("elsewhere") / "x.npy"
    np.save(outside, np.zeros((2, 100)))
    with pytest.raises(PermissionError):
        inspect_record(str(outside), sample_rate_hz=100)


def test_compat_marks_missing_channels_as_mappable():
    manifest = {
        "modality": "eeg",
        "channel_names": ["FP1", "AF3", "F3", "F7", "FC5", "FC1", "C3", "T7", "CP5", "CP1", "P3", "P7", "PO3", "O1", "OZ", "PZ",
                          "FP2", "AF4", "FZ", "F4", "F8", "FC6", "FC2", "CZ", "C4", "T8", "CP6", "CP2", "P4", "P8"],
        "channel_layout": {"value": {}, "source": "file_header"},
        "unit": {"value": "uV", "source": "file_header"},
        "sample_rate_hz": 128,
        "n_channels": 30,
        "duration_s": 60,
    }
    registry = {
        "camer_deap_v1": {"status": "registered", "file_uri": "weights/test.bin", "sha256": "a" * 64, "modalities": ["eeg", "face_video"],
                          "input": {"channel_order": "camer_deap", "sampling_rate_hz": 128, "window_s": 1.0, "supports_channel_mask": True}},
    }
    (r,) = check_model_compat(manifest, registry)
    assert r["status"] == "mappable"
    assert set(c.lower() for c in r["missing_channels"]) == {"po4", "o2"}
    registry["camer_deap_v1"]["input"].pop("supports_channel_mask")
    assert check_model_compat(manifest, registry)[0]["status"] == "incompatible"
    registry["camer_deap_v1"]["input"]["supports_channel_mask"] = True
    registry["camer_deap_v1"].pop("sha256")
    assert check_model_compat(manifest, registry)[0]["status"] == "incompatible"
