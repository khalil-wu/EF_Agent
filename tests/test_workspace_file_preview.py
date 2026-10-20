import pytest
from fastapi.testclient import TestClient

from eegagent.worker import workspaces
from eegagent.worker.app import app


@pytest.mark.parametrize("name,content", [
    ("features.csv", "subject,channel,alpha\na,P3,12.5\n"),
    ("analysis.py", "print('analysis')\n"),
    ("recipe.toml", "window_s = 4\n"),
])
def test_workspace_output_preview_reads_actual_text_without_signal_registration(tmp_path, monkeypatch, name, content):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / name).write_text(content, encoding="utf-8")
    workspace = workspaces.mount(str(folder))
    with TestClient(app) as client:
        response = client.get(f"/workspaces/{workspace['workspace_id']}/preview", params={"path": name})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["content"] == content
    assert result["manifest"]["modality"] == "document"
    assert result["manifest"]["original_name"] == name
    assert result["manifest"]["manifest_id"] == ""
