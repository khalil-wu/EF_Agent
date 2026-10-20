from fastapi.testclient import TestClient

from eegagent.worker import app as worker
from eegagent.worker import workspaces


def test_removing_workspace_preserves_files_history_and_remount_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    folder = tmp_path / "recordings"
    folder.mkdir()
    source = folder / "recording.npz"
    source.write_bytes(b"original acquisition")
    record = workspaces.mount(str(folder), "采集数据")
    identifier = record["workspace_id"]
    workspaces.operations(identifier, "exclude", [source.name])
    client = TestClient(worker.app)
    response = client.delete(f"/workspaces/{identifier}")
    assert response.status_code == 200
    assert client.get("/workspaces").json() == []
    assert source.read_bytes() == b"original acquisition"
    assert workspaces.load(identifier)["excluded"] == [source.name]
    assert client.get(f"/workspaces/{identifier}/file", params={"path": source.name}).content == b"original acquisition"
    restored = workspaces.mount(str(folder))
    assert restored["workspace_id"] == identifier
    assert restored["excluded"] == [source.name]
    assert restored["name"] == "采集数据"
    assert workspaces.list_workspaces() == [restored]


def test_removing_an_unavailable_folder_needs_no_access_to_the_source(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    folder = tmp_path / "offline-drive"
    folder.mkdir()
    record = workspaces.mount(str(folder))
    folder.rmdir()
    client = TestClient(worker.app)
    assert client.delete(f"/workspaces/{record['workspace_id']}").status_code == 200
    assert workspaces.list_workspaces() == []
    assert client.delete("/workspaces/ws_000000000000").status_code == 404
    assert client.delete("/workspaces/invalid-id").status_code == 400
