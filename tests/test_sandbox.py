from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.sandbox import run_python, tail_truncate


def test_stop_terminates_the_script_process(storage):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from eegagent.worker import script_jobs
    sid = "ses_stop_script"
    code = "from pathlib import Path\nimport time\nPath('started.txt').write_text('yes')\ntime.sleep(20)\nPath('must-not-exist.txt').write_text('late write')"
    folder = storage / "scratch" / sid
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_python, code, sid, 40)
        deadline = time.monotonic() + 15
        while not (folder / "started.txt").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (folder / "started.txt").exists()
        assert script_jobs.stop_script(sid)["status"] == "stopped"
        result = future.result(timeout=5)
    assert result["error"]["code"] == "Cancelled"
    assert not (folder / "must-not-exist.txt").exists()


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    st = tmp_path / "storage"
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(st))
    monkeypatch.delenv("EEGAGENT_DATA_DIRS", raising=False)
    from eegagent.worker import signals

    signals._CACHE.clear()
    return store.ensure_layout()


@pytest.fixture()
def manifest(storage: Path) -> dict:
    sr = 250
    t = np.arange(sr * 20) / sr
    data = np.random.default_rng(5).normal(0, 5, (4, len(t)))
    data[3] += 30 * np.sin(2 * np.pi * 10 * t)
    p = storage / "uploads" / "s.npy"
    np.save(p, data)
    return inspect_record(str(p), sample_rate_hz=sr, channel_names=["Fp1", "Fp2", "C3", "O1"])


def test_tail_truncate_keeps_last_lines():
    text = "\n".join(f"line {i}" for i in range(5000))
    out, truncated = tail_truncate(text, max_lines=10, max_bytes=10_000)
    assert truncated and out.splitlines()[-1] == "line 4999" and len(out.splitlines()) == 10


def test_script_copies_readonly_input_into_scratch_without_changing_the_source(storage: Path, manifest: dict, commit_session):
    commit_session("ses_input_copy", [manifest])
    source = Path(manifest["source_path"])
    original = source.read_bytes()
    code = f"import shutil\nshutil.copy2({str(source)!r}, 'input-copy.npy')\n"
    result = run_python(code, session_id="ses_input_copy")
    assert result["status"] == "ok", result.get("error")
    assert source.read_bytes() == original
    assert (storage / "scratch/ses_input_copy/input-copy.npy").read_bytes() == original


def test_script_reads_signal_and_saves_artifacts(storage: Path, manifest: dict, commit_session):
    commit_session("ses_test01", [manifest])
    code = f'''
import numpy as np
from scipy import signal as sps
data, sr, names = eeg.load_signal("{manifest["manifest_id"]}")
print("shape", data.shape, sr, names)
f, p = sps.welch(data, fs=sr, nperseg=int(4*sr), axis=1)
m = (f >= 8) & (f <= 13)
peak = f[m][np.argmax(p[:, m], axis=1)]
tid = eeg.save_table([{{"channel": n, "peak_hz": float(v)}} for n, v in zip(names, peak)], {{"n": len(names)}}, name="peaks")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(); ax.bar(names, peak)
fid = eeg.save_figure(fig, {{"kind": "peaks"}})
open("notes.txt", "w").write("hello")
print("done", tid, fid)
'''
    r = run_python(code, session_id="ses_test01", timeout_s=120, label="peaks")
    assert r["status"] == "ok", r.get("error")
    assert "shape (4," in r["stdout"] and "done" in r["stdout"]
    kinds = {a["kind"] for a in r["artifacts"]}
    assert kinds == {"table", "plot", "report"}
    assert any(a["summary"].get("title") == "notes.txt" for a in r["artifacts"])
    assert "notes.txt" in r["files"]
    assert (storage / "scratch" / "ses_test01" / "run_001.py").exists()
    for a in r["artifacts"]:
        assert Path(a["uri"]).exists()
        assert a["provenance"]["created_by"].startswith("run_python@")


def test_plain_script_deliverables_are_downloadable_immutable_and_keep_sources_after_an_error(storage: Path, manifest: dict, commit_session):
    commit_session("ses_plain_delivery", [manifest])
    from eegagent.worker.outputs import export, read_artifact
    code = f'''
from pathlib import Path
data, sr, names = eeg.load_signal({manifest["manifest_id"]!r})
Path("intervals.csv").write_text("start_s,end_s\\n8,12\\n", encoding="utf-8")
Path("report.md").write_text("# 节律增强\\n8–12 秒。", encoding="utf-8")
Path("analysis.py").write_text("print('replay')", encoding="utf-8")
Path(".private.md").write_text("internal", encoding="utf-8")
Path("run_002.err").write_text("internal stderr", encoding="utf-8")
eeg.save_table([], columns=["start_s", "end_s"], name="empty-intervals")
raise ValueError("后续绘图失败")
'''
    result = run_python(code, session_id="ses_plain_delivery")
    assert result["status"] == "error" and result["error"]["message"] == "后续绘图失败"
    by_title = {a["summary"].get("title"): a for a in result["artifacts"] if a["summary"].get("title")}
    assert set(by_title) == {"intervals.csv", "report.md", "analysis.py"}
    assert len(result["artifacts"]) == 4
    table = by_title["intervals.csv"]
    assert table["source_manifest_ids"] == [manifest["manifest_id"]]
    assert table["provenance"]["params"]["path"] == "intervals.csv"
    original = Path(table["uri"]).read_bytes()
    assert read_artifact(table["artifact_id"], part="rows")["rows"] == [{"start_s": "8", "end_s": "12"}]
    converted = export("json", table["artifact_id"])["artifact"]
    assert read_artifact(converted["artifact_id"], part="rows")["columns"] == ["start_s", "end_s"]
    empty = next(a for a in result["artifacts"] if a["summary"].get("name") == "empty-intervals")
    assert Path(empty["uri"]).read_text(encoding="utf-8").strip() == "start_s,end_s"
    assert read_artifact(empty["artifact_id"], part="rows")["total"] == 0
    second = run_python('from pathlib import Path\nPath("intervals.csv").write_text("start_s,end_s\\n20,24\\n", encoding="utf-8")',
                        session_id="ses_plain_delivery")
    replacement = next(a for a in second["artifacts"] if a["summary"].get("title") == "intervals.csv")
    assert replacement["artifact_id"] != table["artifact_id"]
    assert Path(table["uri"]).read_bytes() == original
    assert Path(replacement["uri"]).read_bytes() != original


def test_script_cannot_write_outside_or_open_sockets(storage: Path, tmp_path_factory: pytest.TempPathFactory):
    outside = tmp_path_factory.mktemp("elsewhere") / "x.txt"
    r = run_python(f'open(r"{outside}", "w").write("x")', session_id="ses_test02")
    assert r["status"] == "error" and "sandbox" in (r["error"]["message"] + r["error"].get("traceback", ""))
    assert not outside.exists()
    r2 = run_python("import socket\ns = socket.socket()", session_id="ses_test02")
    assert r2["status"] == "error" and "socket" in (r2["error"]["message"] + r2["error"].get("traceback", ""))
    r3 = run_python("import subprocess\nsubprocess.run(['echo', 'hi'])", session_id="ses_test02")
    assert r3["status"] == "error"


def test_script_timeout_and_errors_are_reported(storage: Path):
    r = run_python("import time\ntime.sleep(5)", session_id="ses_test03", timeout_s=1)
    assert r["status"] == "error" and r["error"]["code"] == "Timeout"
    r2 = run_python("x = 1 / 0", session_id="ses_test03")
    assert r2["status"] == "error" and r2["error"]["code"] == "ZeroDivisionError" and "1 / 0" in r2["error"]["traceback"]
    assert (storage / "scratch" / "ses_test03" / "run_002.py").exists()


def test_script_uses_mne_ecosystem_and_saves_raw(storage: Path, commit_session):
    from eeg_synth import make_eeg, save_set

    data, sr, names, blink = make_eeg(sr=128, secs=20)
    p = save_set(storage / "uploads" / "r.set", data, sr, names, extra={"EOG1": ("eog", blink)}, annotations=[(5.0, 0.0, "stim")])
    m = inspect_record(str(p))
    commit_session("ses_mne01", [m])
    code = f'''
import numpy as np, mne
from scipy import signal as sps
import mne_icalabel, pyprep, asrpy, autoreject, mne_connectivity, fooof, antropy, eeglabio
from joblib import Parallel, delayed
raw = eeg.to_mne("{m["manifest_id"]}")
mont = raw.get_montage()
print("types", sorted(set(raw.get_channel_types())), "pos", len(mont.get_positions()["ch_pos"]) if mont else 0)
print("ann", list(raw.annotations.description))
raw.filter(1.0, 40.0, picks="eeg")
x = raw.get_data(picks=["O1"])[0] * 1e6
print("hfd", round(float(antropy.higuchi_fd(x)), 3))
f, p = sps.welch(x, fs=raw.info["sfreq"], nperseg=256)
fm = fooof.FOOOF(verbose=False)
fm.fit(f, p, [2, 40])
print("fooof_ok", fm.has_model)
print("joblib", sum(Parallel(n_jobs=2)(delayed(abs)(i) for i in range(4))))
aid = eeg.save_raw(raw, {{"what": "fir 1-40"}}, steps=["bandpass_1_40_fir"], name="filtered")
print("saved", aid)
'''
    r = run_python(code, session_id="ses_mne01", timeout_s=300)
    assert r["status"] == "ok", r.get("error")
    out = r["stdout"]
    assert "types ['eeg', 'eog']" in out and "pos 19" in out and "ann ['stim']" in out
    assert "fooof_ok True" in out and "joblib 6" in out and "hfd" in out
    art = next(a for a in r["artifacts"] if a["kind"] == "normalized_signal")
    rec = store.load_artifact(art["artifact_id"])
    assert rec["provenance"]["steps"][-1] == "bandpass_1_40_fir" and rec["signal"]["channel_types"][-1] == "eog"
    from eegagent.worker.processing import segment_signal

    assert segment_signal(art["artifact_id"], window_s=2)["summary"]["n_windows"] == 10
