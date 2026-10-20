"""Native folder selection attaches the original directory, or changes nothing on cancel."""
from fastapi.testclient import TestClient

from eegagent.worker import app as worker
from eegagent.worker import workspaces


def test_native_folder_selection_mounts_original_absolute_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    folder = tmp_path / "采集数据 01"
    folder.mkdir()
    original = folder / "recording.npz"
    original.write_bytes(b"original acquisition fixture")
    monkeypatch.setattr(worker, "pick_directory", lambda: str(folder.resolve()))
    response = TestClient(worker.app).post("/workspaces/pick", json={})
    assert response.status_code == 200
    record = response.json()
    assert record["root"] == str(folder.resolve())
    assert record["workspace_id"].startswith("ws_")
    assert workspaces.load(record["workspace_id"])["root"] == str(folder.resolve())
    assert original.read_bytes() == b"original acquisition fixture"
    assert not (tmp_path / "storage" / "uploads").exists()


def test_native_folder_selection_cancel_preserves_workspace_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    folder = tmp_path / "existing-recordings"
    folder.mkdir()
    existing = workspaces.mount(str(folder))
    monkeypatch.setattr(worker, "pick_directory", lambda: None)
    response = TestClient(worker.app).post("/workspaces/pick", json={})
    assert response.status_code == 200 and response.json() == {"cancelled": True}
    assert workspaces.list_workspaces() == [existing]


def test_native_folder_selection_reports_picker_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))

    def unavailable():
        raise RuntimeError("native dialog could not initialize")

    monkeypatch.setattr(worker, "pick_directory", unavailable)
    response = TestClient(worker.app).post("/workspaces/pick", json={})
    assert response.status_code == 500
    assert response.json() == {"error": "native dialog could not initialize"}
    assert workspaces.list_workspaces() == []
