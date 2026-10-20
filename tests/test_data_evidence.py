"""Format recognition, recorded evidence and numerical interpretation are distinct contracts."""

import pickle

import numpy as np
import pytest

from eegagent.worker import signals, store
from eegagent.worker.channel_orders import DEAP_MAHNOB_CHANNEL_ORDER
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.registry import check_model_compat


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    return store.ensure_layout()


def save_deap_shape(path, **metadata):
    data = np.random.default_rng(5).normal(0, 10, (2, 40, 8064))
    content = {"data": data, "labels": np.array([[6, 4, 3, 7], [2, 8, 5, 1]]), **metadata}
    if path.suffix == ".dat":
        with path.open("wb") as file:
            pickle.dump(content, file, protocol=2)
    else:
        from scipy.io import savemat

        savemat(path, content)
    return data


@pytest.mark.parametrize("extension", [".dat", ".mat"])
def test_matching_shape_uses_the_format_for_measurement_without_confirming_origin(storage, extension):
    path = storage / "uploads" / ("official_DEAP" + extension)
    data = save_deap_shape(path)
    record = inspect_record(str(path))

    assert record["format_profile"] == "deap_preprocessed"
    assert record["dataset"] == "unknown"
    assert record["sample_rate_hz"] == 128 and record["duration_s"] == 63
    assert record["reference"] == "unknown"
    assert record["channel_names"] == list(DEAP_MAHNOB_CHANNEL_ORDER)
    assert record["channel_layout"]["source"] == "format_assumption"
    assert record["labels"] == {"values": [6, 4, 3, 7]}
    evidence = record["metadata_evidence"]
    assert evidence["origin"] == {"value": "unverified", "source": "unknown"}
    assert evidence["data_shape"] == {"value": [2, 40, 8064], "source": "file_content"}
    for key in ("sample_rate_hz", "channel_names", "reference", "preprocessing", "label_names"):
        assert evidence[key]["source"] == "format_assumption"
    assert evidence['unit'] == {'value': 'unknown', 'source': 'unknown'}
    assert record["unit"]["source"] == "inferred"
    assert "confidence" not in record["unit"]
    values, rate, names = signals.load_signal(record["manifest_id"])
    np.testing.assert_allclose(values, data[0, :32])
    assert rate == 128 and names == list(DEAP_MAHNOB_CHANNEL_ORDER)
    assert "sample_rate_format_assumption" in signals.resolve_signal(record["manifest_id"])[3]
    registry = {"model": {"status": "registered", "file_uri": "model.pt", "sha256": "a" * 64,
                           "modalities": ["eeg"], "input": {"channel_order": "camer_deap", "sampling_rate_hz": 128}}}
    assert check_model_compat(record, registry)[0]["status"] == "incompatible"


@pytest.mark.parametrize("extension", [".dat", ".mat"])
def test_confirmation_enables_computation_without_authenticating_origin(storage, extension):
    path = storage / "uploads" / ("study" + extension)
    original_data = save_deap_shape(path)
    original = inspect_record(str(path), trial_index=1)
    names = list(DEAP_MAHNOB_CHANNEL_ORDER)
    updated = update_record_metadata(original["manifest_id"], sample_rate_hz=256,
                                     channel_names=names, unit="mV", dataset="DEAP",
                                     reference="average", baseline_duration_s=3,
                                     label_names=["rating_a", "rating_b", "rating_c", "rating_d"])["manifest"]
    values, rate, loaded_names = signals.load_signal(updated["manifest_id"])
    np.testing.assert_allclose(values, original_data[1, :32] * 1000)
    assert rate == 256 and loaded_names == names
    assert updated["duration_s"] == 31.5 and updated["trial_index"] == 1
    assert updated["labels"]["names"] == ["rating_a", "rating_b", "rating_c", "rating_d"]
    assert updated["labels"]["values"] == [2, 8, 5, 1]
    assert updated["metadata_evidence"]["dataset"] == {"value": "DEAP", "source": "user_declared"}
    assert updated["metadata_evidence"]["origin"]["value"] == "unverified"
    assert updated["metadata_evidence"]["sample_rate_hz"]["source"] == "user_confirmed"
    assert "sample_rate_format_assumption" not in signals.resolve_signal(updated["manifest_id"])[3]
    assert store.load_manifest(original["manifest_id"])["sample_rate_hz"] == 128
    assert store.load_manifest(original["manifest_id"])["metadata_evidence"]["sample_rate_hz"]["source"] == "format_assumption"


@pytest.mark.parametrize("extension", [".dat", ".mat"])
def test_explicit_file_metadata_is_a_header_declaration_not_official_provenance(storage, extension):
    path = storage / "uploads" / ("declared" + extension)
    names = list(DEAP_MAHNOB_CHANNEL_ORDER)
    original = save_deap_shape(path, fs=200, unit="mV", ch_names=np.array(names),
                               label_names=np.array(["a", "b", "c", "d"]), reference="average")
    record = inspect_record(str(path))
    assert record["sample_rate_hz"] == 200
    assert record["channel_names"] == names
    assert record["unit"] == {"value": "mV", "source": "file_header"}
    assert record["labels"]["names"] == ["a", "b", "c", "d"]
    assert record["dataset"] == "unknown"
    updated = update_record_metadata(record["manifest_id"], dataset="DEAP")["manifest"]
    assert updated["metadata_evidence"]["sample_rate_hz"]["source"] == "file_header"
    np.testing.assert_allclose(signals.load_signal(updated["manifest_id"])[0], original[0, :32] * 1000)


def test_legacy_adapter_values_stay_assumptions_until_each_field_is_confirmed(storage):
    path = storage / "uploads" / "legacy.dat"
    save_deap_shape(path)
    legacy = inspect_record(str(path))
    legacy.pop("metadata_evidence")
    legacy.update(dataset="DEAP", sample_rate_hz=128, duration_s=63,
                  channel_names=list(DEAP_MAHNOB_CHANNEL_ORDER),
                  channel_layout={"value": {}, "source": "dataset_adapter"},
                  unit={"value": "uV", "source": "dataset_adapter"})
    store.save_manifest(legacy)
    registry = {"model": {"status": "registered", "file_uri": "model.pt", "sha256": "a" * 64,
                           "modalities": ["eeg"], "input": {"channel_order": "camer_deap", "sampling_rate_hz": 128}}}
    assert check_model_compat(legacy, registry)[0]["status"] == "incompatible"
    assert {"unit_format_assumption", "sample_rate_format_assumption"} <= set(signals.resolve_signal(legacy["manifest_id"])[3])
    updated = update_record_metadata(legacy["manifest_id"], unit="uV")["manifest"]
    assert updated["metadata_evidence"]["sample_rate_hz"]["source"] == "format_assumption"
    assert updated["metadata_evidence"]["channel_names"]["source"] == "format_assumption"
    assert updated["metadata_evidence"]["dataset"]["source"] == "format_assumption"
    assert check_model_compat(updated, registry)[0]["status"] == "incompatible"
    confirmed = update_record_metadata(updated["manifest_id"], sample_rate_hz=128,
                                       channel_names=list(DEAP_MAHNOB_CHANNEL_ORDER))["manifest"]
    assert check_model_compat(confirmed, registry)[0]["status"] == "compatible"
    assert "sample_rate_format_assumption" not in signals.resolve_signal(confirmed["manifest_id"])[3]


def test_native_voltage_conversion_does_not_rescale_non_voltage_channels(storage):
    path = storage / "uploads" / "mixed.npz"
    values = np.vstack([np.linspace(-1e-5, 1e-5, 256), np.arange(256)])
    np.savez(path, data=values, fs=128, unit="V", ch_names=["Fz", "GSR"])
    record = inspect_record(str(path))
    converted, _, _ = signals.load_signal(record["manifest_id"])
    np.testing.assert_allclose(converted[0], values[0] * 1e6)
    np.testing.assert_array_equal(converted[1], values[1])


def test_inspect_existing_record_keeps_user_metadata(storage):
    from eegagent.worker.tools import run_tool

    path = storage / "uploads" / "confirmed.npy"
    np.save(path, np.random.default_rng(5).normal(size=(2, 1000)))
    original = inspect_record(str(path), sample_rate_hz=250, channel_names=["O1", "O2"])
    confirmed = update_record_metadata(original["manifest_id"], unit="mV", reference="Cz")["manifest"]
    inspected = run_tool("inspect_record", {"manifest_id": confirmed["manifest_id"]})["manifest"]
    assert inspected["manifest_id"] == confirmed["manifest_id"]
    assert inspected["unit"] == {"value": "mV", "source": "user_confirmed"}
    assert inspected["reference"] == "Cz"
    np.testing.assert_allclose(signals.load_signal(inspected["manifest_id"])[0], np.load(path) * 1000)
