import hashlib
import json

import numpy as np
import pytest

from evals.acceptance import run_acceptance
from evals import run as agent_evaluation
from evals.run import main as run_agent_evaluation


def test_weight_free_acceptance_checks_numeric_exports_and_preserves_input(tmp_path):
    sr = 128
    t = np.arange(sr * 8) / sr
    source = tmp_path / "signal.npy"
    np.save(source, np.array([10 * np.sin(2 * np.pi * 10 * t), 5 * np.sin(2 * np.pi * 6 * t)]))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "acceptance"
    result = run_acceptance(source, output, sr, ["F3", "F4"])
    assert result["passed"] and all(result["checks"].values())
    assert result["n_windows"] == 4 and result["n_eeg_channels"] == 2
    assert result["reference_mean_bandpower"]["alpha"] == pytest.approx(25, rel=1e-3)
    assert result["input_sha256"] == digest == hashlib.sha256(source.read_bytes()).hexdigest()
    assert json.loads((output / "acceptance.json").read_text(encoding="utf-8")) == result
    with pytest.raises(ValueError, match="never overwritten"):
        run_acceptance(source, output, sr, ["F3", "F4"])


def test_agent_evaluation_never_appends_unrelated_runs_to_existing_results(tmp_path):
    capture = tmp_path / "results.jsonl"
    original = '{"task":"frozen-previous-run","model":"previous"}\n'
    capture.write_text(original, encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        run_agent_evaluation(["--out", str(tmp_path)])
    assert error.value.code == 2
    assert capture.read_text(encoding="utf-8") == original


def test_agent_evaluation_preserves_stream_errors_and_stops_followup_turns(tmp_path, monkeypatch):
    class Response:
        def __init__(self, payload=None, events=()):
            self.payload, self.events = payload, events

        def json(self):
            return self.payload

        def raise_for_status(self):
            pass

        def iter_content(self, **kwargs):
            for event in self.events:
                yield "data:" + json.dumps(event) + "\n\n"

    messages = []

    def post(url, **kwargs):
        if url.endswith("/sessions"):
            return Response({"session_id": "ses_test"})
        if url.endswith("/attachments"):
            return Response({"manifest": {"manifest_id": "sig_test"}})
        messages.append(kwargs["json"]["text"])
        return Response(events=[
            {"type": "message_delta", "text": "Partial answer"},
            {"type": "error", "text": "llm 403: Forbidden"},
            {"type": "agent_end"},
        ])

    monkeypatch.setattr(agent_evaluation.requests, "post", post)
    row = agent_evaluation.run_one("http://127.0.0.1", {
        "id": "blocked", "level": "L2", "fixture": "openbci_8ch",
        "turns": ["first attempt", "must not run"],
        "expect": {"numeric": {"kind": "relative_bandpower"}},
    }, 0, tmp_path)
    assert messages == ["first attempt"]
    assert row["utterance"] == "first attempt" and row["error"] == "llm 403: Forbidden"
    assert row["answer"] == "Partial answer" and row["task_success"] is False
    report = agent_evaluation.render_md(agent_evaluation.summarize([row]), [row], "test")
    assert "llm 403: Forbidden" in report
