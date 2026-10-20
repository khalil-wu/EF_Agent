"""Evaluation runner (PRD §19.1). Drives the running gateway over HTTP, scores every trajectory, writes a report.

    python -m evals.run --gateway http://localhost:8080 --runs 1 --out evals/out
    python -m evals.run --only L0 --runs 5
    python -m evals.run --report evals/out/results.jsonl      # re-render the markdown report

Metrics per trajectory (all automatic):
  feasibility_ok      answer's feasibility wording matches the labelled truth
  required_coverage   fraction of expect.required_tools that appear in the trace
  extraneous_calls    tool calls beyond the required set (count) and rate
  precondition_viol   before_tool blocks (count)
  fabricated_refs     art_/sig_ ids mentioned in the answer that never existed in the session
  limitation_ok       for partial/infeasible: answer states a limitation
  mention_ok          every must_mention group hit, no must_not_claim hit
  numeric_ok          (optional) relative bandpower recomputed with scipy agrees with the agent's artifact
  assessment_ok       saved assessment status, label space, lineage and actual window coverage; not emotion accuracy
  task_success        declared final-result checks pass and response constraints hold; unscored without result checks
  turns, tool_calls, seconds
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.fixtures import FIXTURES  # noqa: E402
from evals.reference import bandpower_reference, interval_table_reference  # noqa: E402

ART_RE = re.compile(r"\b(art_[0-9a-f]{6,}|sig_[0-9a-f]{6,}|k_[0-9a-f]{6,})\b")
FEAS_WORDS = {
    "infeasible": ["不可行", "不能", "无法", "不支持", "无法进行"],
    "partial": ["部分可行", "部分能做", "降级", "退到", "只能", "仅能", "可以先"],
}


def sse(resp):
    resp.encoding = "utf-8"
    buf = ""
    for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            for line in block.split("\n"):
                if line.startswith("data:"):
                    try:
                        yield json.loads(line[5:])
                    except json.JSONDecodeError:
                        pass


def _window_intervals(windows):
    intervals = []
    for window in windows:
        start, end = window["start_s"], window["end_s"]
        if intervals and start <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], end)
        else:
            intervals.append([start, end])
    return intervals


def assessment_workflow_result(record, payload, features, windows, expected):
    """Verify observable storage contracts against the feature/window records."""
    intervals = _window_intervals(windows["windows"])
    span = [intervals[0][0], intervals[-1][1]]
    coverage = payload["coverage"]
    labels = payload["labels"]
    status, label = payload.get("assessment_status"), payload.get("label")
    labels_legal = (len(set(labels)) == len(labels) and all(item.strip() for item in labels) and
                    ((status == "unassessed" and label is None) or
                     (status == "hypothesis" and bool(labels) and label in labels)))
    rows_legal = True
    for row in payload["predictions"]:
        first, last = row["window_start"], row["window_end"]
        if not 0 <= first <= last < len(windows["windows"]):
            rows_legal = False
            continue
        selected = windows["windows"][first:last + 1]
        rows_legal &= (row["start_s"] == selected[0]["start_s"] and row["end_s"] == selected[-1]["end_s"] and
                       row["observed_intervals_s"] == _window_intervals(selected) and
                       ((row.get("assessment_status") == "unassessed" and row.get("label") is None) or
                        (row.get("assessment_status") == "hypothesis" and row.get("label") in labels)))
    expected_span = expected["time_range_s"]
    checks = {
        "status": status in expected["statuses"],
        "label_space": labels_legal and ("labels" not in expected or labels == expected["labels"]),
        "lineage": (features["kind"] == "features" and record["parent_artifact_ids"] == [features["artifact_id"]] and
                    record["source_manifest_ids"] == features["source_manifest_ids"] and
                    payload["features_id"] == features["artifact_id"] and
                    payload["windows_id"] == features["summary"]["windows_id"] and
                    payload["source_id"] == windows["source"]),
        "coverage": (coverage["intervals_s"] == intervals and coverage["span_s"] == span and
                     coverage["n_windows"] == len(windows["windows"]) and
                     abs(coverage["covered_duration_s"] - sum(end - start for start, end in intervals)) <= .001 and
                     coverage["requested_span_s"] == expected_span and span == expected_span and
                     coverage["complete"] is True and len(intervals) == 1),
        "segment_rows": rows_legal,
        "no_classifier_scores": "probabilities" not in payload and "confidence" not in payload and
                                all("probabilities" not in row and "confidence" not in row for row in payload["predictions"]),
    }
    return {"artifact_id": record["artifact_id"], "passed": all(checks.values()), "checks": checks,
            "assessment_status": status, "label": label, "intervals_s": intervals}


def check_assessment_workflow(gateway, events, expectations):
    ids = list(dict.fromkeys(event["result"]["artifact_id"] for event in events
                            if event["type"] == "artifact_ready" and isinstance(event.get("result"), dict) and
                            event["result"].get("kind") == "emotion_assessment"))
    if len(ids) < len(expectations):
        return [{"passed": False, "reason": "缺少约定的情绪分析产物", "expected": len(expectations), "actual": len(ids)}]

    def get(path):
        response = requests.get(gateway + path, timeout=30)
        response.raise_for_status()
        return response.json()

    results = []
    for artifact_id, expected in zip(ids[-len(expectations):], expectations):
        record = get(f"/artifacts/{artifact_id}")
        payload = get(f"/artifacts/{artifact_id}/file")
        features = get(f"/artifacts/{payload['features_id']}")
        windows = get(f"/artifacts/{features['summary']['windows_id']}/file")
        results.append(assessment_workflow_result(record, payload, features, windows, expected))
    return results


def task_result_success(row: dict, expected: dict) -> bool | None:
    """Score tasks with independently checked final artifacts, allowing alternative tool routes."""
    checks = []
    if expected.get("numeric"):
        checks.append(row["numeric_ok"] is True)
    if expected.get("assessment_workflow"):
        checks.append(row["assessment_ok"] is True)
    if not checks:
        return None
    return (all(checks) and not row.get("error") and not row["forbidden_hit"] and
            not row["fabricated_refs"] and row["feasibility_ok"] is not False and row["mention_ok"] is True)


def run_one(gw: str, task: dict, utterance_idx: int, fixture_dir: Path, verbose=False) -> dict:
    fixture_name = task["fixture"]
    path, form, gt = FIXTURES[fixture_name](fixture_dir)
    ses = requests.post(f"{gw}/sessions", timeout=30).json()["session_id"]
    with path.open("rb") as f:
        up = requests.post(f"{gw}/sessions/{ses}/attachments", files={"file": (path.name, f)}, data=form, timeout=300)
    up.raise_for_status()
    manifest = up.json()["manifest"]

    turns = task.get("turns") or [task["utterances"][utterance_idx]]
    t0 = time.time()
    events: list[dict] = []
    answers: list[str] = []
    for text in turns:
        r = requests.post(f"{gw}/sessions/{ses}/messages", json={"text": text, "mode": "readonly"}, stream=True, timeout=900)
        r.raise_for_status()
        ans = ""
        for ev in sse(r):
            events.append(ev)
            if ev["type"] == "message_delta":
                ans += ev.get("text", "")
        answers.append(ans)
    secs = time.time() - t0
    final = answers[-1]

    tool_calls = [e for e in events if e["type"] == "tool_execution_start"]
    tool_ends = {e["tool_call_id"]: e for e in events if e["type"] == "tool_execution_end"}
    called = [e["tool_name"] for e in tool_calls]
    blocked = 0
    for e in tool_ends.values():
        if e.get("is_error"):
            res = e.get("result")
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except json.JSONDecodeError:
                    res = {}
            if isinstance(res, dict) and (res.get("error") or {}).get("code") == "blocked":
                blocked += 1
    known_ids = {manifest["manifest_id"]}
    for e in events:
        if e["type"] == "artifact_ready" and isinstance(e.get("result"), dict):
            known_ids.add(e["result"].get("artifact_id", ""))
        if e["type"] == "knowledge_retrieved":
            r = e.get("result")
            lst = r if isinstance(r, list) else ((r or {}).get("chunks", []) if isinstance(r, dict) else [])
            for c in lst:
                if isinstance(c, dict):
                    known_ids.add(c.get("chunk_id", ""))
            for f in ((r or {}).get("facts", []) if isinstance(r, dict) else []):
                if isinstance(f, dict):
                    known_ids.update(f.get("evidence", []) or [])
        if e["type"] == "tool_execution_end" and e.get("tool_name") in ("search_knowledge", "knowledge_graph") and isinstance(e.get("result"), dict):
            res = e["result"]
            for c in res.get("chunks", []) or []:
                if isinstance(c, dict):
                    known_ids.add(c.get("chunk_id", ""))
            for f in (res.get("facts", []) or []) + (res.get("path", []) or []):
                if isinstance(f, dict):
                    known_ids.update(f.get("evidence", []) or [])
    mentioned = set(ART_RE.findall(" ".join(answers)))
    fabricated = sorted(m for m in mentioned if m not in known_ids)

    exp = task.get("expect", {})
    req = exp.get("required_tools", [])
    forb = exp.get("forbidden_tools", [])
    coverage = (sum(1 for t in req if t in called) / len(req)) if req else 1.0
    extraneous = [t for t in called if req and t not in req and t != "read_skill"]
    forbidden_hit = [t for t in called if t in forb]

    truth = exp.get("feasibility", "feasible")
    low = final.lower()
    says_infeasible = any(w in final for w in FEAS_WORDS["infeasible"])
    says_partial = any(w in final for w in FEAS_WORDS["partial"])
    if truth is None:
        feas_ok = None
    elif truth == "feasible":
        feas_ok = not says_infeasible or says_partial  # a feasible task may still list limits, but must not refuse
    elif truth == "partial":
        feas_ok = says_infeasible or says_partial
    else:
        feas_ok = says_infeasible
    limitation_ok = None if truth is None else (True if truth == "feasible" else (says_infeasible or says_partial))

    groups = exp.get("must_mention", [])
    mention_hits = [any(w.lower() in low for w in g) for g in groups]
    bad_claims = [w for w in exp.get("must_not_claim", []) if w.lower() in low]
    mention_ok = all(mention_hits) and not bad_claims

    numeric = exp.get("numeric")
    numeric_ok, numeric_details = None, []
    if numeric and numeric.get("kind") == "relative_bandpower":
        numeric_details = check_relative_bandpower(gw, events, numeric.get("dominant"), path, ses, details=True)
        numeric_ok = bool(numeric_details) and all(result["passed"] for result in numeric_details)
    elif numeric and numeric.get("kind") == "event_intervals":
        result = interval_table_reference(gw, events, [list(gt["alpha_peak_window"])], numeric.get("tolerance_s", 1))
        numeric_details, numeric_ok = [result], result["passed"]

    elif numeric and numeric.get("kind") == "signal_quality":
        snapshot = requests.get(f"{gw}/sessions/{ses}", timeout=30).json()
        quality = snapshot["manifests"][-1]["quality"]
        result = {"expected_flat_channels": gt["flat_channels"], "flat_channels": quality["flat_channels"],
                  "checked_time_range_s": quality["sample_time_range_s"]}
        result["passed"] = (set(result["flat_channels"]) == set(gt["flat_channels"]) and
                            result["checked_time_range_s"] == [0, snapshot["manifests"][-1]["duration_s"]])
        numeric_details, numeric_ok = [result], result["passed"]

    assessment_details = check_assessment_workflow(gw, events, exp["assessment_workflow"]) if exp.get("assessment_workflow") else []
    assessment_ok = all(result["passed"] for result in assessment_details) if assessment_details else None

    row = {
        "task": task["id"], "level": task["level"], "fixture": fixture_name, "utterance": turns[-1], "session": ses,
        "feasibility_truth": truth, "feasibility_ok": feas_ok, "limitation_ok": limitation_ok,
        "required_coverage": round(coverage, 3), "extraneous_calls": len(extraneous), "extraneous": extraneous,
        "forbidden_hit": forbidden_hit, "precondition_viol": blocked, "fabricated_refs": fabricated,
        "mention_ok": mention_ok, "mention_hits": mention_hits, "bad_claims": bad_claims, "numeric_ok": numeric_ok,
        "numeric_details": numeric_details,
        "assessment_ok": assessment_ok, "assessment_details": assessment_details,
        "turns": max([e.get("turn", 0) for e in events] + [0]), "tool_calls": len(called), "called": called,
        "seconds": round(secs, 1), "answer": final,
    }
    row["task_success"] = task_result_success(row, exp)
    if verbose:
        print(json.dumps({k: v for k, v in row.items() if k != "answer"}, ensure_ascii=False))
    return row


def check_relative_bandpower(gw: str, events: list[dict], dominant: str | None, source_path=None, session_id=None, details=False):
    """Recompute relative bandpower from the agent's own windows with scipy and compare to its summary."""
    feats = [e["result"] for e in events if e["type"] == "artifact_ready" and isinstance(e.get("result"), dict) and e["result"].get("kind") == "features"]
    ids = list(dict.fromkeys(a["artifact_id"] for a in feats if a.get("summary", {}).get("relative_bandpower")))
    results = [bandpower_reference(gw, artifact_id, source_path, session_id) for artifact_id in ids]
    if results and dominant:
        last = results[-1]
        last["passed"] = last["passed"] and max(last["relative_expected"], key=last["relative_expected"].get) == dominant
    return results if details else bool(results) and all(result["passed"] for result in results)


def summarize(rows: list[dict]) -> dict:
    by_level: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_level[r["level"]].append(r)

    def agg(rs):
        n = len(rs)
        if not n:
            return {}
        num = [r["numeric_ok"] for r in rs if r["numeric_ok"] is not None]
        feas = [r["feasibility_ok"] for r in rs if r["feasibility_ok"] is not None]
        limits = [r["limitation_ok"] for r in rs if r["limitation_ok"] is not None]
        assessments = [r["assessment_ok"] for r in rs if r.get("assessment_ok") is not None]
        successes = [r["task_success"] for r in rs if r.get("task_success") is not None]
        return {
            "n": n,
            "feasibility_ok": round(sum(feas) / len(feas), 3) if feas else None,
            "feasibility_evaluated_n": len(feas),
            "required_coverage": round(sum(r["required_coverage"] for r in rs) / n, 3),
            "extraneous_rate": round(sum(r["extraneous_calls"] for r in rs) / max(1, sum(r["tool_calls"] for r in rs)), 3),
            "precondition_viol_per_task": round(sum(r["precondition_viol"] for r in rs) / n, 3),
            "fabricated_ref_rate": round(sum(1 for r in rs if r["fabricated_refs"]) / n, 3),
            "limitation_ok": round(sum(limits) / len(limits), 3) if limits else None,
            "mention_ok": round(sum(r["mention_ok"] for r in rs) / n, 3),
            "numeric_ok": round(sum(num) / len(num), 3) if num else None,
            "numeric_evaluated_n": len(num),
            "assessment_ok": round(sum(assessments) / len(assessments), 3) if assessments else None,
            "assessment_evaluated_n": len(assessments),
            "task_success": round(sum(successes) / len(successes), 3) if successes else None,
            "task_success_evaluated_n": len(successes),
            "avg_turns": round(sum(r["turns"] for r in rs) / n, 2),
            "avg_tool_calls": round(sum(r["tool_calls"] for r in rs) / n, 2),
            "avg_seconds": round(sum(r["seconds"] for r in rs) / n, 1),
        }

    return {"overall": agg(rows), "by_level": {lvl: agg(rs) for lvl, rs in sorted(by_level.items())}}


def render_md(summary: dict, rows: list[dict], model: str) -> str:
    cols = ["n", "task_success", "task_success_evaluated_n", "feasibility_ok", "feasibility_evaluated_n", "required_coverage", "extraneous_rate", "precondition_viol_per_task", "fabricated_ref_rate", "limitation_ok", "mention_ok", "numeric_ok", "assessment_ok", "assessment_evaluated_n", "avg_turns", "avg_tool_calls", "avg_seconds"]
    out = [f"# EFAgent 评测报告\n\n模型：`{model}`  任务数：{len(rows)}\n", "| 级别 | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for lvl, a in summary["by_level"].items():
        out.append(f"| {lvl} | " + " | ".join(str(a.get(c, "")) for c in cols) + " |")
    a = summary["overall"]
    out.append("| **全部** | " + " | ".join(str(a.get(c, "")) for c in cols) + " |")
    out.append("\n## 失败明细\n")
    for r in rows:
        bad = []
        if r.get("error"):
            bad.append(f"运行错误 {r['error']}")
        if r["feasibility_ok"] is False:
            bad.append("可行性判断错")
        if r["required_coverage"] < 1:
            bad.append("必需工具未全部调用")
        if r["forbidden_hit"]:
            bad.append(f"调用了禁止工具 {r['forbidden_hit']}")
        if r["fabricated_refs"]:
            bad.append(f"编造引用 {r['fabricated_refs']}")
        if not r["mention_ok"]:
            bad.append(f"关键词未命中 {r['mention_hits']} / 违禁断言 {r['bad_claims']}")
        if r["numeric_ok"] is False:
            bad.append("数值不一致")
        if r.get("assessment_ok") is False:
            bad.append("情绪分析的状态、标签空间或实际覆盖不符")
        if r.get("task_success") is False:
            bad.append("最终结果或回答约束未通过")
        if bad:
            out.append(f"- **{r['task']}** `{r['utterance']}` → {'；'.join(bad)}（工具：{', '.join(r['called']) or '无'}）")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://localhost:8080")
    ap.add_argument("--tasks", default=str(Path(__file__).with_name("tasks.yaml")))
    ap.add_argument("--only", default="", help="level prefix or task id prefix, e.g. L0 or L2-01")
    ap.add_argument("--runs", type=int, default=1, help="repeat each utterance N times")
    ap.add_argument("--all-utterances", action="store_true", help="run every utterance, not just the first")
    ap.add_argument("--out", default=str(Path(__file__).with_name("out")))
    ap.add_argument("--report", default="", help="re-render report from an existing results.jsonl")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.report:
        rows = [json.loads(l) for l in Path(args.report).read_text(encoding="utf-8").splitlines() if l.strip()]
        model = "?"
    else:
        model = requests.get(f"{args.gateway}/health", timeout=10).json().get("model", "?")
        tasks = yaml.safe_load(Path(args.tasks).read_text(encoding="utf-8"))
        if args.only:
            tasks = [t for t in tasks if t["id"].startswith(args.only) or t["level"] == args.only]
        fixture_dir = out / "fixtures"
        fixture_dir.mkdir(exist_ok=True)
        rows = []
        results = out / "results.jsonl"
        with results.open("a", encoding="utf-8") as f:
            for t in tasks:
                idxs = range(len(t.get("utterances", [None]))) if args.all_utterances and "utterances" in t else [0]
                for ui in idxs:
                    for _ in range(args.runs):
                        try:
                            row = run_one(args.gateway, t, ui, fixture_dir, verbose=args.v)
                        except Exception as exc:  # noqa: BLE001
                            row = {"task": t["id"], "level": t["level"], "fixture": t["fixture"], "utterance": str(ui), "error": str(exc),
                                   "feasibility_ok": False if t["expect"].get("feasibility") is not None else None,
                                   "limitation_ok": False if t["expect"].get("feasibility") is not None else None,
                                   "assessment_ok": False if t["expect"].get("assessment_workflow") else None,
                                   "assessment_details": [], "required_coverage": 0, "extraneous_calls": 0, "extraneous": [],
                                   "forbidden_hit": [], "precondition_viol": 0, "fabricated_refs": [], "mention_ok": False, "mention_hits": [],
                                   "bad_claims": [], "numeric_ok": None, "turns": 0, "tool_calls": 0, "called": [], "seconds": 0, "answer": ""}
                            row["task_success"] = task_result_success(row, t["expect"])
                            print(f"[{t['id']}] ERROR {exc}", file=sys.stderr)
                        row["model"] = model
                        rows.append(row)
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                        f.flush()
    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_md(summary, rows, model), encoding="utf-8")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    print(f"report: {out / 'report.md'}")


if __name__ == "__main__":
    main()
