from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import store
from eegagent.worker.files import list_files, read_file, resolve_inside_roots, scratch_dir
from eegagent.worker.inspect_record import inspect_record


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = tmp_path / "storage"
    data = tmp_path / "data"
    data.mkdir()
    (data / "sub").mkdir()
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(storage))
    monkeypatch.setenv("EEGAGENT_DATA_DIRS", str(data))
    store.ensure_layout()
    np.save(data / "sub" / "rec_a.npy", np.random.default_rng(0).normal(0, 10, (4, 2500)))
    (data / "notes.txt").write_text("1,2,3\n4,5,6\n", encoding="utf-8")
    (data / "README.md").write_text("\n".join(f"line {i}" for i in range(300)), encoding="utf-8")
    np.save(storage / "uploads" / "up.npy", np.zeros((2, 500)))
    (tmp_path / "outside.npy").write_bytes(b"x")
    return storage, data, tmp_path


def test_list_files_finds_eeg_in_roots(roots):
    storage, data, _ = roots
    r = list_files(kind="eeg")
    names = {f["name"] for f in r["files"]}
    assert {"rec_a.npy", "up.npy"} <= names and "README.md" not in names and "notes.txt" not in names
    assert str(data.resolve()) in r["roots"]
    assert [f["name"] for f in list_files(pattern="rec_*")["files"]] == ["rec_a.npy"]
    assert "README.md" in {f["name"] for f in list_files(kind="all")["files"]}


def test_list_files_includes_session_scratch(roots):
    sd = scratch_dir("ses_abc")
    (sd / "run_001.out").write_text("x", encoding="utf-8")
    r = list_files(kind="all", session_id="ses_abc")
    assert "run_001.out" in {f["name"] for f in r["files"]}


def test_paths_outside_roots_are_rejected(roots):
    _, _, tmp = roots
    with pytest.raises(PermissionError):
        resolve_inside_roots(tmp / "outside.npy")
    assert list_files(root=str(tmp))["status"] == "error"
    with pytest.raises(PermissionError):
        read_file(str(tmp / "outside.npy"))


def test_read_file_pages_and_rejects_binary(roots):
    _, data, _ = roots
    r = read_file(str(data / "README.md"), offset=1, limit=100)
    assert r["status"] == "ok" and r["total_lines"] == 300 and r["end_line"] == 100 and r["truncated"]
    assert r["content"].splitlines()[0] == "line 0" and "offset=101" in r["note"]
    r2 = read_file(str(data / "README.md"), offset=291, limit=100)
    assert r2["end_line"] == 300 and not r2["truncated"]
    assert read_file(str(data / "sub" / "rec_a.npy"))["status"] == "error"


def test_inspect_record_accepts_data_dir_files(roots):
    _, data, _ = roots
    m = inspect_record(str(data / "sub" / "rec_a.npy"), sample_rate_hz=250)
    assert m["n_channels"] == 4 and m["duration_s"] == 10.0


def test_relative_reads_search_the_data_roots_and_decode_chinese_acquisition_text(roots):
    _, data, _ = roots
    content = "采样率：250 Hz\r\n参考电极：双乳突\r\n"
    (data / "采集说明.txt").write_bytes(content.encode("gb18030"))
    result = read_file("采集说明.txt", offset=2, limit=1)
    assert Path(result["path"]) == data / "采集说明.txt"
    assert result["content"] == "参考电极：双乳突"
    assert result["encoding"] == "gb18030"
    assert result["total_lines"] == 2 and not result["truncated"]
