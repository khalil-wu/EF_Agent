from pathlib import Path

import numpy as np
import yaml

from evals.fixtures import FIXTURES
from evals.run import assessment_workflow_result, render_md, summarize, task_result_success


def test_final_result_failure_is_not_hidden_by_tool_coverage_or_unscored_tasks():
    row = {'numeric_ok': False, 'assessment_ok': None, 'required_coverage': 1,
           'forbidden_hit': [], 'fabricated_refs': [], 'feasibility_ok': True, 'mention_ok': True}
    expected = {'numeric': {'kind': 'relative_bandpower'}}
    assert task_result_success(row, expected) is False
    row.update(numeric_ok=True, required_coverage=0)
    assert task_result_success(row, expected) is True  # alternative tools can produce correct final results
    assert task_result_success(row, {'required_tools': ['plot']}) is None
    row.update(error='execution failed', numeric_ok=None)
    assert task_result_success(row, expected) is False


def test_fixtures_generate(tmp_path: Path):
    for name, fn in FIXTURES.items():
        p, form, gt = fn(tmp_path)
        assert p.exists() and p.stat().st_size > 0, name
        if p.suffix == ".npy":
            arr = np.load(p)
            assert arr.ndim == 2 and arr.shape[0] < arr.shape[1]


def test_summarize_handles_levels():
    rows = [
        {"level": "L0", "feasibility_ok": True, "required_coverage": 1, "extraneous_calls": 0, "tool_calls": 2, "precondition_viol": 0,
         "fabricated_refs": [], "limitation_ok": True, "mention_ok": True, "numeric_ok": None, "turns": 2, "seconds": 3},
        {"level": "L2", "feasibility_ok": False, "required_coverage": 0.5, "extraneous_calls": 1, "tool_calls": 4, "precondition_viol": 1,
         "fabricated_refs": ["art_deadbeef"], "limitation_ok": True, "mention_ok": False, "numeric_ok": True, "turns": 4, "seconds": 9},
    ]
    s = summarize(rows)
    assert s["overall"]["n"] == 2 and s["by_level"]["L2"]["fabricated_ref_rate"] == 1.0
    assert s["overall"]["feasibility_ok"] == 0.5


def test_emotion_workflow_accepts_unassessed_without_scoring_it_as_emotion_accuracy():
    record = {"artifact_id": "art_assessment", "parent_artifact_ids": ["art_features"], "source_manifest_ids": ["sig_signal"]}
    features = {"artifact_id": "art_features", "kind": "features", "source_manifest_ids": ["sig_signal"],
                "summary": {"windows_id": "art_windows"}}
    windows = {"source": "sig_signal", "windows": [{"start_s": 10, "end_s": 14}, {"start_s": 12, "end_s": 16}]}
    payload = {"features_id": "art_features", "windows_id": "art_windows", "source_id": "sig_signal", "assessment_status": "unassessed",
               "label": None, "labels": [], "predictions": [],
               "coverage": {"intervals_s": [[10, 16]], "span_s": [10, 16], "n_windows": 2,
                            "covered_duration_s": 6, "requested_span_s": [10, 16], "complete": True}}
    expected = {"statuses": ["unassessed", "hypothesis"], "time_range_s": [10, 16]}
    assert assessment_workflow_result(record, payload, features, windows, expected)["passed"]
    payload["assessment_status"] = "hypothesis"
    payload["labels"], payload["label"] = ["positive", "negative"], "positive"
    assert assessment_workflow_result(record, payload, features, windows, expected)["passed"]
    payload["label"] = "invented"
    assert not assessment_workflow_result(record, payload, features, windows, expected)["passed"]
    payload["assessment_status"], payload["label"] = "unassessed", None
    payload["coverage"]["intervals_s"] = [[0, 60]]
    assert not assessment_workflow_result(record, payload, features, windows, expected)["checks"]["coverage"]
    assert "accuracy" not in assessment_workflow_result(record, payload, features, windows, expected)


def test_emotion_tasks_do_not_require_certainty_or_fixed_four_quadrants():
    tasks = yaml.safe_load(Path("evals/tasks.yaml").read_text(encoding="utf-8"))
    emotions = [task for task in tasks if task["expect"].get("assessment_workflow")]
    assert len(emotions) == 4
    for task in emotions:
        assert task["expect"]["feasibility"] is None
        assert "不能可靠识别" not in task["expect"].get("must_not_claim", [])
        for expectation in task["expect"]["assessment_workflow"]:
            assert "unassessed" in expectation["statuses"]


def test_summary_excludes_unscored_emotion_feasibility_and_counts_saved_workflows():
    row = {"task": "L0-01", "level": "L0", "utterance": "情绪", "feasibility_ok": None, "limitation_ok": None,
           "required_coverage": 1, "extraneous_calls": 0, "tool_calls": 4, "precondition_viol": 0,
           "fabricated_refs": [], "mention_ok": True, "mention_hits": [], "bad_claims": [], "numeric_ok": None,
           "assessment_ok": True, "turns": 2, "seconds": 3, "forbidden_hit": [], "called": []}
    summary = summarize([row])
    assert summary["overall"]["feasibility_ok"] is None
    assert summary["overall"]["feasibility_evaluated_n"] == 0
    assert summary["overall"]["assessment_ok"] == 1
    assert summary["overall"]["assessment_evaluated_n"] == 1
    assert "可行性判断错" not in render_md(summary, [row], "test")
