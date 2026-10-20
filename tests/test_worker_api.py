import json
from eegagent.worker import app as worker_app


def test_missing_tool_and_missing_argument_are_different():
    unknown = worker_app.call_tool("not_a_tool", worker_app.ToolCall())
    missing = worker_app.call_tool("inspect_record", worker_app.ToolCall())
    assert unknown.status_code == 404
    assert json.loads(unknown.body)["error"]["code"] == "unknown_tool"
    assert missing.status_code == 400
    assert json.loads(missing.body)["error"]["code"] == "missing_argument"


def test_health_exposes_broken_knowledge_index(monkeypatch):
    def broken_index():
        raise ValueError("invalid index")
    monkeypatch.setattr(worker_app.knowledge, "index", broken_index)
    result = worker_app.health()
    assert result["status"] == "degraded"
    assert result["knowledge"]["message"] == "invalid index"


def test_missing_optional_dependency_is_reported_as_unavailable(monkeypatch):
    def unavailable(*args):
        raise ModuleNotFoundError("example_optional_package")
    monkeypatch.setattr(worker_app,"run_tool",unavailable)
    result=worker_app.call_tool("clean_artifacts",worker_app.ToolCall())
    assert result.status_code==400
    error=json.loads(result.body)["error"]
    assert error["code"]=="dependency_unavailable" and error["recoverable"]


def test_known_ica_download_has_semantic_chinese_name_and_real_trial(tmp_path, monkeypatch):
    from urllib.parse import unquote
    from eegagent.worker import store
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    store.save_manifest({"manifest_id": "sig_fixture", "source_path": "D:/records/s02.dat", "trial_index": 0})
    path = store.artifact_payload_path("art_abcdef", "png")
    path.write_bytes(b"image-payload")
    store.save_artifact({"artifact_id": "art_abcdef", "kind": "plot", "uri": str(path), "media_type": "image/png",
                         "source_manifest_ids": ["sig_fixture"], "summary": {"kind": "clean_ica_iclabel"}})
    response = worker_app.get_artifact_file("art_abcdef")
    assert "ICA成分头皮分布_s02_试次1_版本abcdef.png" in unquote(response.headers["content-disposition"])
    assert response.path == str(path) and path.read_bytes() == b"image-payload"
