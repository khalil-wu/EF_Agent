"""A workspace goal can compose existing tools and deliver actual files."""
import csv
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import signals, store, workspaces
from eegagent.worker.progress import channel
from eegagent.worker.sandbox import run_python
from eegagent.worker.sandbox_api import EEGApi
from eegagent.worker.tools import run_tool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.delenv("EEGAGENT_DATA_DIRS", raising=False)
    signals._CACHE.clear()
    store.ensure_layout()
    folder = tmp_path / "recordings"
    folder.mkdir()
    record = workspaces.mount(str(folder))
    context = {"_session_id": "file_tools_fixture", "_workspace_id": record["workspace_id"]}
    return folder, record, context


def test_workspace_text_creation_exact_edit_and_explicit_overwrite(workspace):
    folder, record, context = workspace
    result = run_tool("write_file", {**context, "path": "analysis/protocol.md", "content": "α=8–13 Hz\r\nα=8–13 Hz\r\n"})
    target = folder / "analysis/protocol.md"
    original = target.read_bytes()
    assert result["workspace_file"] == {"workspace_id": record["workspace_id"], "path": "analysis/protocol.md", "name": "protocol.md", "kind": "file"}
    assert Path(result["path"]) == target
    with pytest.raises(FileExistsError):
        run_tool("write_file", {**context, "path": "analysis/protocol.md", "content": "unrequested replacement"})
    assert target.read_bytes() == original
    with pytest.raises(ValueError, match="匹配 2 处"):
        run_tool("edit_file", {**context, "path": "analysis/protocol.md", "old_text": "8–13", "new_text": "8–12"})
    assert target.read_bytes() == original
    edited = run_tool("edit_file", {**context, "path": "analysis/protocol.md", "old_text": "8–13", "new_text": "8–12", "replace_all": True})
    assert edited["replacements"] == 2 and target.read_bytes().count(b"\r\n") == 2
    assert "8–12" in run_tool("read_file", {**context, "path": "analysis/protocol.md"})["content"]
    run_tool("write_file", {**context, "path": "analysis/protocol.md", "content": "explicit replacement", "overwrite": True})
    assert target.read_text(encoding="utf-8") == "explicit replacement"
    with pytest.raises(ValueError):
        run_tool("write_file", {**context, "path": "../outside.txt", "content": "escape"})
    with pytest.raises(ValueError, match="相对"):
        run_tool("write_file", {**context, "path": str(target), "content": "absolute"})
    assert not (folder.parent / "outside.txt").exists()


def test_save_real_artifact_to_workspace_preserves_source_and_overwrite_contract(workspace):
    folder, record, context = workspace
    api = EEGApi(context["_session_id"], store.ensure_layout() / "scratch", "test", "save-fixture")
    identifier = api.save_table([{"subject": "被试 01", "alpha": 1.25}], name="features")
    source = Path(store.load_artifact(identifier)["uri"])
    original = source.read_bytes()
    result = run_tool("save_to_workspace", {**context, "artifact_id": identifier, "path": "results/features.csv"})
    assert result["artifact_id"] == identifier
    assert result["workspace_file"]["path"] == "results/features.csv"
    assert result["workspace_file"]["artifact_id"] == identifier
    assert (folder / "results/features.csv").read_bytes() == original == source.read_bytes()
    with pytest.raises(FileExistsError):
        run_tool("save_to_workspace", {**context, "artifact_id": identifier, "path": "results/features.csv"})
    (folder / "results/features.csv").write_text("old result", encoding="utf-8")
    run_tool("save_to_workspace", {**context, "artifact_id": identifier, "path": "results/features.csv", "overwrite": True})
    assert (folder / "results/features.csv").read_bytes() == original


def test_workspace_text_edit_keeps_gb18030_encoding_and_original_newlines(workspace):
    folder, _, context = workspace
    path = folder / "采集说明.txt"
    path.write_bytes("采样率：250 Hz\r\n参考：双乳突\r\n".encode("gb18030"))
    assert "参考：双乳突" in run_tool("read_file", {**context, "path": path.name})["content"]
    run_tool("edit_file", {**context, "path": path.name, "old_text": "250 Hz", "new_text": "500 Hz"})
    assert path.read_bytes() == "采样率：500 Hz\r\n参考：双乳突\r\n".encode("gb18030")


def test_script_composes_native_tools_across_files_and_streams_real_outputs(workspace):
    folder, record, context = workspace
    t = np.arange(1024) / 128
    for index in (1, 2, 4):
        data = np.array([np.sin(2*np.pi*10*t) + 3*np.sin(2*np.pi*50*t), np.cos(2*np.pi*10*t)])
        np.savez(folder / f"{index:02}.npz", data=data, fs=128, ch_names=["P3", "P4"], unit="uV")
    (folder / "03.npz").write_bytes(b"invalid recording")
    workspaces.operations(record["workspace_id"], "exclude", ["04.npz"])
    originals = {path.name: path.read_bytes() for path in folder.glob("*.npz")}
    code = '''
import time
from pathlib import Path
eeg.call("write_file", path="results/protocol.md", content="Bandpass 1–35 Hz; 2-second non-overlapping windows")
time.sleep(.4)
files, offset = [], 0
while True:
    page = eeg.call("list_files", pattern="*.npz", limit=1, offset=offset)
    files.extend(page["files"])
    if page["next_offset"] is None:
        break
    offset = page["next_offset"]
rows = []
for file in files:
    try:
        manifest = eeg.call("inspect_record", path=file["path"])["manifest"]
        source = eeg.call("preprocess", source_id=manifest["manifest_id"], steps=[{"op":"bandpass","low":1,"high":35}])["artifact"]["artifact_id"]
        windows = eeg.call("segment_signal", source_id=source, window_s=2)["artifact"]["artifact_id"]
        features = eeg.call("extract_features", windows_id=windows, features=["bandpower","hjorth"])["artifact"]["artifact_id"]
        table = eeg.call("export", kind="csv", source_id=features)["artifact"]["artifact_id"]
        eeg.call("save_to_workspace", artifact_id=table, path="results/" + Path(file["path"]).stem + "-features.csv")
        rows.append({"file":file["name"], "status":"processed"})
    except (ValueError, OSError) as error:
        rows.append({"file":file["name"], "status":"failed", "error":str(error)})
summary = eeg.save_table(rows, {"files":len(files)}, name="batch-summary")
eeg.call("save_to_workspace", artifact_id=summary, path="results/summary.csv")
eeg.call("edit_file", path="results/protocol.md", old_text="2-second", new_text="2-second (verified)")
print("processed", sum(row["status"]=="processed" for row in rows), "failed", sum(row["status"]=="failed" for row in rows))
'''
    stages, early = [], []

    def observe(event):
        stages.append(event)
        early.append(not (folder / "results/summary.csv").exists())

    with channel(observe):
        result = run_tool("run_python", {**context, "code": code, "timeout_s": 120, "label": "复用科学工具处理合成采集数据"})
    assert result["status"] == "ok", result.get("error")
    assert "processed 2 failed 1" in result["stdout"]
    assert len(result["manifests"]) == 2
    assert {item["kind"] for item in result["artifacts"]} >= {"normalized_signal", "windows", "features", "table"}
    assert len({item["artifact_id"] for item in result["artifacts"]}) == len(result["artifacts"])
    assert {item["path"] for item in result["workspace_files"]} == {"results/protocol.md", "results/01-features.csv", "results/02-features.csv", "results/summary.csv"}
    assert stages and any(early) and len({item["stage"] for item in stages}) > 10
    assert all(item["stage"].startswith("script_call_") for item in stages)
    with (folder / "results/summary.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["status"] for row in rows] == ["processed", "processed", "failed"]
    assert all((folder / name).read_bytes() == data for name, data in originals.items())
    processed = next(item for item in result["artifacts"] if item["kind"] == "normalized_signal")
    assert np.load(processed["uri"])[0].std() < .6 * np.load(folder / "01.npz")["data"][0].std()


def test_script_native_scope_ends_at_call_and_rejects_remote_or_recursive_tools(workspace):
    folder, record, context = workspace
    outside = folder.parent / "other-recordings"
    outside.mkdir()
    (outside / "secret.txt").write_text("other workspace", encoding="utf-8")
    workspaces.mount(str(outside))
    code = f'''
from pathlib import Path
eeg.call("write_file", path="protocol.md", content="authorized output")
for name in ("run_python", "run_model", "recognize_emotion"):
    try:
        eeg.call(name)
    except ValueError:
        print("not a local tool", name)
    else:
        raise AssertionError(name)
for path in ({str(folder / 'direct-write.txt')!r}, {str(store.storage_dir() / 'manifests' / 'direct.json')!r}):
    try:
        Path(path).write_text("direct mutation", encoding="utf-8")
    except PermissionError:
        print("native scope restored")
    else:
        raise AssertionError(path)
try:
    eeg.call("read_file", path={str(outside / 'secret.txt')!r})
except PermissionError:
    print("current workspace only")
else:
    raise AssertionError("read another mounted workspace")
print(eeg.call("read_file", path="protocol.md")["content"])
'''
    result = run_python(code, context["_session_id"], workspace_id=record["workspace_id"])
    assert result["status"] == "ok", result.get("error")
    assert result["stdout"].count("native scope restored") == 2
    assert "current workspace only" in result["stdout"] and "authorized output" in result["stdout"]
    assert not (folder / "direct-write.txt").exists()
    assert not (store.storage_dir() / "manifests/direct.json").exists()


def test_stop_keeps_completed_native_outputs_and_prevents_later_writes(workspace):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    import time
    from eegagent.worker.app import app

    folder, record, context = workspace
    t = np.arange(512) / 128
    np.savez(folder / "input.npz", data=np.array([np.sin(2*np.pi*10*t), np.cos(2*np.pi*10*t)]),
             fs=128, ch_names=["P3", "P4"], unit="uV")
    code = '''
from pathlib import Path
import time
manifest = eeg.call("inspect_record", path="input.npz")["manifest"]["manifest_id"]
windows = eeg.call("segment_signal", source_id=manifest, window_s=2)["artifact"]["artifact_id"]
features = eeg.call("extract_features", windows_id=windows, features=["bandpower"])["artifact"]["artifact_id"]
table = eeg.call("export", kind="csv", source_id=features)["artifact"]["artifact_id"]
eeg.call("save_to_workspace", artifact_id=table, path="results/completed.csv")
Path("ready.txt").write_text("ready")
time.sleep(20)
eeg.call("write_file", path="results/must-not-exist.md", content="late output")
'''
    scratch = store.storage_dir() / "scratch" / context["_session_id"]
    with ThreadPoolExecutor() as pool:
        future = pool.submit(run_python, code, context["_session_id"], 40, None, record["workspace_id"])
        deadline = time.monotonic() + 20
        while not (scratch / "ready.txt").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (scratch / "ready.txt").exists()
        stopped = TestClient(app).post(f"/sessions/{context['_session_id']}/stop").json()
        assert stopped["status"] == "stopped"
        result = future.result(timeout=5)
    assert stopped["result"] == result
    assert result["status"] == "error" and result["error"]["code"] == "Cancelled"
    assert len(result["manifests"]) == 1
    assert {item["kind"] for item in result["artifacts"]} >= {"windows", "features", "table"}
    assert [item["path"] for item in result["workspace_files"]] == ["results/completed.csv"]
    assert (folder / "results/completed.csv").exists()
    assert not (folder / "results/must-not-exist.md").exists()


def test_reusing_script_with_different_input_keeps_each_runs_artifact(workspace):
    folder, record, context = workspace
    for name, value in [("first.npz", 1.0), ("second.npz", 2.0)]:
        np.savez(folder / name, data=np.full((2, 256), value), fs=128, ch_names=["P3", "P4"], unit="uV")
    code = '''
source = eeg.call("read_file", path="input.txt")["content"].strip()
manifest = eeg.call("inspect_record", path=source)["manifest"]["manifest_id"]
data, sr, names = eeg.load_signal(manifest)
eeg.save_table([{"mean":float(data.mean())}], name="same-script")
'''
    run_tool("write_file", {**context, "path": "input.txt", "content": "first.npz"})
    first = run_python(code, context["_session_id"], workspace_id=record["workspace_id"])
    assert first["status"] == "ok", first.get("error")
    first_artifact = first["artifacts"][0]
    first_bytes = Path(first_artifact["uri"]).read_bytes()
    run_tool("edit_file", {**context, "path": "input.txt", "old_text": "first.npz", "new_text": "second.npz"})
    second = run_python(code, context["_session_id"], workspace_id=record["workspace_id"])
    assert second["status"] == "ok", second.get("error")
    second_artifact = second["artifacts"][0]
    assert first_artifact["artifact_id"] != second_artifact["artifact_id"]
    assert first_artifact["source_manifest_ids"] != second_artifact["source_manifest_ids"]
    assert Path(first_artifact["uri"]).read_bytes() == first_bytes
    assert Path(second_artifact["uri"]).read_bytes() != first_bytes
