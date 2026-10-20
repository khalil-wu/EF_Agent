import pickle
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import store
from eegagent.worker.channels import clean_name, guess_channel_type, merge_channel_types
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.readers import epochs_to_continuous, read_deap_dat, resolve_main_file
from eeg_synth import make_eeg, save_set


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.delenv("EEGAGENT_DATA_DIRS", raising=False)
    from eegagent.worker import signals

    signals._CACHE.clear()
    return store.ensure_layout()


def test_channel_name_cleaning_and_types():
    assert clean_name("EEG Fp1-REF") == "Fp1" and clean_name("FP1.") == "FP1" and clean_name("C3-A2") == "C3"
    assert clean_name("RAW_TP9") == "TP9" and clean_name("Fp1-F7") == "Fp1-F7"  # bipolar pairs stay
    assert guess_channel_type("Status") == "stim" and guess_channel_type("STI 014") == "stim"
    assert guess_channel_type("VEOG") == "eog" and guess_channel_type("hEOG") == "eog" and guess_channel_type("EKG") == "ecg"
    assert guess_channel_type("EXG Channel 0") == "eeg"  # OpenBCI names its EEG inputs this way
    assert guess_channel_type("Accel Channel 1") == "misc" and guess_channel_type("Sample Index") == "misc"
    assert guess_channel_type("EXG3") == "misc"  # BioSemi external electrodes
    assert merge_channel_types(["Fp1", "EOG1", "Status"], ["eeg", "eeg", "stim"]) == ["eeg", "eog", "stim"]


def test_eeglab_set_is_read_with_types_events_and_full_signal(storage: Path):
    data, sr, names, blink = make_eeg(sr=128, secs=30)
    p = save_set(storage / "uploads" / "rec.set", data, sr, names,
                 extra={"EOG1": ("eog", blink), "STATUS": ("stim", np.zeros(data.shape[1]))},
                 annotations=[(5.0, 1.0, "eyes_closed"), (20.0, 0.0, "stim")])
    m = inspect_record(str(p))
    assert m["source_format"] == "set" and m["sample_rate_hz"] == 128 and m["n_channels"] == 21
    assert m["duration_s"] == 30.0 and m["unit"] == {"value": "uV", "source": "file_header", "confidence": 1.0}
    assert m["channel_types"][-2:] == ["eog", "stim"] and m["channel_layout"]["source"] == "file_header"
    assert [e["label"] for e in m["events"]] == ["eyes_closed", "stim"] and m["events"][0]["onset_s"] == 5.0
    assert any("非 EEG 通道" in w for w in m["warnings"])
    from eegagent.worker.signals import load_signal

    full, fsr, fnames = load_signal(m["manifest_id"])
    assert full.shape == (21, 128 * 30) and fnames[:2] == ["Fp1", "Fp2"]
    assert np.allclose(full[:19], data, atol=1e-3)  # µV round trip through the .set file


def test_pick_eeg_uses_channel_types_and_fir_bandpass(storage: Path):
    from eegagent.worker.processing import preprocess

    data, sr, names, blink = make_eeg(sr=128, secs=20)
    p = save_set(storage / "uploads" / "rec.set", data, sr, names, extra={"EOG1": ("eog", blink), "STATUS": ("stim", np.zeros(data.shape[1]))})
    m = inspect_record(str(p))
    out = preprocess(m["manifest_id"], [{"op": "pick_eeg"}, {"op": "bandpass", "low": 1, "high": 40, "method": "fir"}])
    art = out["artifact"]
    assert art["shape"][0] == 19 and art["provenance"]["steps"][-2:] == ["pick_eeg", "bandpass_1_40_fir"]
    rec = store.load_artifact(art["artifact_id"])
    assert rec["signal"]["channel_types"] == ["eeg"] * 19
    assert "EOG1(eog)" in out["summary"]["notes"][0]


def test_fif_and_epoched_data(storage: Path):
    import mne

    data, sr, names, _ = make_eeg(sr=128, secs=12, blinks=False)
    raw = mne.io.RawArray(data * 1e-6, mne.create_info(names, sr, "eeg"), verbose="ERROR")
    fif = storage / "uploads" / "rec_raw.fif"
    raw.save(fif, overwrite=True, verbose="ERROR")
    m = inspect_record(str(fif))
    assert m["source_format"] == "fif" and m["n_samples"] == 128 * 12

    ev = mne.make_fixed_length_events(raw, duration=2.0)
    ep = mne.Epochs(raw, ev, tmin=0, tmax=2.0 - 1 / sr, baseline=None, preload=True, verbose="ERROR")
    cont, events, note = epochs_to_continuous(ep)
    assert cont.shape == (19, len(ep) * 256) and len(events) == len(ep) and events[1]["onset_s"] == 2.0
    assert "epoch" in note


def test_mat_heuristics(storage: Path):
    from scipy.io import savemat

    rng = np.random.default_rng(0)
    p = storage / "uploads" / "lab_export.mat"
    savemat(p, {"data": rng.normal(0, 20, (2500, 4)), "srate": 250.0, "ch_names": np.array(["Fz", "Cz", "Pz", "Oz"], dtype=object)})
    m = inspect_record(str(p))
    assert m["sample_rate_hz"] == 250 and m["channel_names"] == ["Fz", "Cz", "Pz", "Oz"] and m["n_samples"] == 2500
    seed = storage / "uploads" / "seed_like.mat"
    savemat(seed, {"djc_eeg1": rng.normal(0, 20, (4, 1000)), "djc_eeg2": rng.normal(0, 20, (4, 1200))})
    m2 = inspect_record(str(seed), sample_rate_hz=200, trial_index=1)
    assert m2["n_samples"] == 1200 and m2["trial_index"] == 1 and any("djc_eeg2" in w for w in m2["warnings"])
    from eegagent.worker.signals import load_signal

    assert load_signal(m2["manifest_id"])[0].shape == (4, 1200)


def test_mat_cell_joined_data(storage: Path):
    """AMIGOS 风格：trial 矩阵装在 cell 里（joined_data{1..20}），读取须能进 cell。"""
    from scipy.io import savemat

    rng = np.random.default_rng(2)
    cells = np.empty((1, 3), dtype=object)
    for i in range(3):
        cells[0, i] = rng.normal(0, 20, (1000 + i * 50, 5))  # [samples, channels]
    p = storage / "uploads" / "amigos_like.mat"
    savemat(p, {"joined_data": cells, "VideoIDs": np.array(["10", "13", "138"], dtype=object)})
    m = inspect_record(str(p), sample_rate_hz=128, trial_index=1)
    assert m["n_channels"] == 5 and m["n_samples"] == 1050 and m["trial_index"] == 1
    assert m["trial_id"] == "joined_data[1]"
    assert any("3 个同通道数矩阵" in w for w in m["warnings"])
    # 越界 trial_index 报错
    with pytest.raises(Exception):
        inspect_record(str(p), sample_rate_hz=128, trial_index=3)


def test_openbci_gui_txt(storage: Path):
    p = storage / "uploads" / "OpenBCI-RAW.txt"
    rng = np.random.default_rng(1)
    lines = ["%OpenBCI Raw EXG Data", "%Number of channels = 4", "%Sample Rate = 250 Hz", "%Board = OpenBCI_GUI$BoardCytonSerial",
             "Sample Index, EXG Channel 0, EXG Channel 1, EXG Channel 2, EXG Channel 3, Accel Channel 0, Marker Channel, Timestamp (Formatted)"]
    for i in range(2500):
        v = rng.normal(0, 15, 4)
        lines.append(f"{i % 256}, {v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}, {v[3]:.3f}, 0.01, 0, 2026-09-23 10:00:00.000")
    p.write_text("\n".join(lines), encoding="utf-8")
    m = inspect_record(str(p))
    assert m["sample_rate_hz"] == 250 and m["duration_s"] == 10.0
    assert m["channel_types"] == ["misc", "eeg", "eeg", "eeg", "eeg", "misc", "stim"]
    assert m["unit"]["value"] == "uV"  # inferred from the EXG columns only, not the sample counter
    assert any("非数值列" in w for w in m["warnings"])


def test_deap_pickle_is_loaded_safely(tmp_path: Path):
    ok = tmp_path / "s.dat"
    with ok.open("wb") as f:
        pickle.dump({"data": np.zeros((2, 3, 4)), "labels": np.ones((2, 4))}, f, protocol=2)
    data, labels = read_deap_dat(ok)
    assert data.shape == (2, 3, 4) and labels.shape == (2, 4)

    class Evil:
        def __reduce__(self):
            import os

            return (os.system, ("echo pwned",))

    bad = tmp_path / "evil.dat"
    with bad.open("wb") as f:
        pickle.dump({"data": Evil()}, f, protocol=2)
    with pytest.raises(pickle.UnpicklingError):
        read_deap_dat(bad)


def test_sidecar_files_resolve_to_their_header(tmp_path: Path):
    (tmp_path / "a.vhdr").write_text("x")
    assert resolve_main_file(tmp_path / "a.vmrk") == tmp_path / "a.vhdr"
    assert resolve_main_file(tmp_path / "a.eeg") == tmp_path / "a.vhdr"
    with pytest.raises(FileNotFoundError):
        resolve_main_file(tmp_path / "b.fdt")
