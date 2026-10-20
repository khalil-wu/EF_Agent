"""The skill's linked API recipes must run as written inside the sandbox."""

import re
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.sandbox import run_python
from eeg_synth import make_eeg, save_set

SKILL = Path(__file__).resolve().parents[1] / "skills" / "eeg_toolboxes" / "SKILL.md"


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.delenv("EEGAGENT_DATA_DIRS", raising=False)
    from eegagent.worker import signals

    signals._CACHE.clear()
    return store.ensure_layout()


def _event_data():
    """60 s, 19 channels, events A/B every 1.5 s; B carries a P300-like bump over parietal channels."""
    data, sr, names, _ = make_eeg(sr=128, secs=60, blinks=False)
    t_ev = np.arange(2.0, 58.0, 1.5)
    labels = ["A" if i % 2 == 0 else "B" for i in range(len(t_ev))]
    w = np.arange(int(0.6 * sr))
    bump = 12 * np.exp(-0.5 * ((w / sr - 0.35) / 0.06) ** 2)
    parietal = [names.index(c) for c in ("Pz", "P3", "P4", "Cz")]
    for t, lab in zip(t_ev, labels):
        if lab == "B":
            i = int(t * sr)
            data[parietal, i:i + len(w)] += bump
    return data, sr, names, [(float(t), 0.0, lab) for t, lab in zip(t_ev, labels)]


def test_skill_recipes_run_in_sandbox(storage: Path):
    data, sr, names, ann = _event_data()
    m = inspect_record(str(save_set(storage / "uploads" / "erp.set", data, sr, names, annotations=ann)))
    assert len(m["events"]) == len(ann)
    assert "reference=api.md" in SKILL.read_text(encoding="utf-8")
    recipes = SKILL.parent / "references" / "api.md"
    blocks = re.findall(r"```python\n(.*?)```", recipes.read_text(encoding="utf-8"), flags=re.S)
    assert len(blocks) >= 6
    # Native epoch reconstruction has its own .set fixture in test_epoch_semantics.
    code = "\n".join(block for block in blocks if "eeg.to_epochs" not in block).replace("SOURCE_ID", m["manifest_id"])
    r = run_python(code, session_id="ses_recipes", timeout_s=600, label="skill recipes")
    assert r["status"] == "ok", (r.get("error") or {}).get("traceback", r.get("error"))
    kinds = [a["kind"] for a in r["artifacts"]]
    assert kinds.count("plot") >= 2 and kinds.count("table") >= 2 and "normalized_signal" in kinds
    assert "epochs rejected" in r["stdout"] and "'A'" in r["stdout"]
    peaks = next(a for a in r["artifacts"] if a["kind"] == "table" and a["summary"].get("name") == "erp_peaks")
    rows = Path(peaks["uri"]).read_text(encoding="utf-8")
    assert "B" in rows
    fooof = next(a for a in r["artifacts"] if a["kind"] == "table" and a["summary"].get("name") == "fooof")
    o1 = next(line for line in Path(fooof["uri"]).read_text(encoding="utf-8").splitlines() if line.startswith("O1,"))
    assert 9.5 <= float(o1.split(",")[1]) <= 10.5  # the synthetic alpha source is 10 Hz
    assert "Filtering raw data" not in r["stdout"]  # MNE INFO logging is off in the sandbox
