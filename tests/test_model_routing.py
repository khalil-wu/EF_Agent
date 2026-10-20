import pytest

from eegagent.worker import models
from eegagent.worker.store import save_manifest
from eegagent.worker.tools import run_tool


@pytest.fixture
def eeg():
    return {"manifest_id": "sig_test", "modality": "eeg", "n_channels": 2,
            "channel_names": ["C3", "C4"], "channel_layout": {"source": "file_header"},
            "unit": {"source": "file_header", "value": "uV"},
            "sample_rate_hz": 128, "duration_s": 120}


@pytest.fixture
def registry():
    return {"eegnet_test": {"task": "emotion", "status": "registered", "modalities": ["eeg"],
                            "file_uri": "weights/test.pt", "sha256": "a" * 64,
                            "input": {"channels": 2, "sampling_rate_hz": 128, "window_s": 1},
                            "label_map": ["low", "high"]}}


def test_registered_weights_do_not_imply_an_inference_executor(registry, eeg):
    catalog = models.list_models("emotion", registry)
    assert catalog["models"][0]["availability"] == "adapter_pending"
    result = models.route_model("emotion", eeg, registry=registry)
    assert result["selected_model"] is None
    assert result["candidates"][0]["compatibility"] == "compatible"
    assert not result["prediction_executed"]


def test_router_selects_ready_model_but_does_not_execute_prediction(monkeypatch, registry, eeg):
    monkeypatch.setattr(models, "EXECUTABLE_MODELS", {"eegnet_test"})
    result = models.route_model("emotion", eeg, registry=registry)
    assert result["selected_model"] == "eegnet_test"
    assert result["candidates"][0]["route_status"] == "ready"
    assert result["prediction_executed"] is False
    eeg["sample_rate_hz"] = 256
    result = models.route_model("emotion", eeg, registry=registry)
    assert result["selected_model"] is None
    assert result["candidates"][0]["route_status"] == "needs_preparation"


def test_sleep_task_never_routes_to_an_emotion_classifier(registry, eeg):
    assert models.route_model("sleep_stage", eeg, registry=registry)["candidates"] == []
    with pytest.raises(ValueError, match="不支持任务"):
        models.route_model("sleep_stage", eeg, model_id="eegnet_test", registry=registry)


def test_unfilled_training_contract_cannot_be_ready(monkeypatch, registry, eeg):
    monkeypatch.setattr(models, "EXECUTABLE_MODELS", {"eegnet_test"})
    registry["eegnet_test"]["input_contract_status"] = "pending"
    assert models.route_model("emotion", eeg, registry=registry)["selected_model"] is None


def test_multimodal_route_requires_face_role_sync_and_actual_overlap(monkeypatch, registry, eeg):
    monkeypatch.setattr(models, "EXECUTABLE_MODELS", {"eegnet_test"})
    registry["eegnet_test"]["modalities"] = ["eeg", "face_video"]
    registry["eegnet_test"]["input"]["sync_required"] = True
    video = {"manifest_id": "vid_test", "modality": "video", "duration_s": 120, "timing": {"status": "cfr_verified"}}
    result = models.route_model("emotion", eeg, registry=registry)
    assert len(result["candidates"][0]["missing_context"]) == 2
    result = models.route_model("emotion", eeg, video, "stimulus", 0, registry=registry)
    assert result["selected_model"] is None
    assert any("刺激视频" in reason for reason in result["candidates"][0]["reasons"])
    assert models.route_model("emotion", eeg, video, "face", 0, registry=registry)["selected_model"] == "eegnet_test"
    result = models.route_model("emotion", eeg, video, "face", 150, registry=registry)
    assert result["selected_model"] is None
    assert any("重叠时长" in reason for reason in result["candidates"][0]["reasons"])


def test_worker_dispatch_distinguishes_registered_executor_and_pending_models(tmp_path, monkeypatch, eeg):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    save_manifest(eeg)
    result = run_tool("route_model", {"task": "emotion", "manifest_id": "sig_test"})
    assert result["selected_model"] is None
    assert {row["model_id"] for row in result["candidates"]} >= {"camer_deap_v1", "eegnet_emotion_custom"}
    available = [row for row in result["candidates"] if row["inference_available"]]
    assert {row["model_id"] for row in available} == {"eav_eegnet_emotion5_loso_fold0", "camer_eav_photo_fixed_fold0", "camer_deap_fixed_1s_fold0"}
    assert available[0]["compatibility"] == "incompatible"
    sleep = run_tool("list_models", {"task": "sleep_stage"})
    assert sleep["models"][0]["model_id"] == "sleep_stager_custom"


def test_research_only_catalog_entries_do_not_crash_or_route(eeg):
    for task in ("eeg_language_model", "embedding_baseline"):
        catalog = models.list_models(task)
        assert catalog["models"][0]["availability"] == "research_only"
        result = models.route_model(task, eeg)
        assert result["selected_model"] is None and result["candidates"] == []


# Inference is mocked; use isolated registered contracts rather than local weights.
pytestmark = pytest.mark.usefixtures("registered_model_registry")
