"""Behaviour probes: multi-file uploads, format defaults, interpretation direction and answer style.

    python -m evals.probes --gateway http://localhost:8080 --runs 2 --out evals/out/probes
    python -m evals.probes --score evals/out/results.jsonl      # answer-style metrics for any results file

Each probe uploads its own files (one request per file, like the web client) and records upload outcomes,
user-input requests, tool calls, the final answer and keyword checks. The answer-style metrics count generic
hedging phrases per 1000 answer characters; they describe wording, not scientific correctness.
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import requests
import yaml

from evals.run import sse

HEDGES = ["不构成", "不能当作", "不能作为", "不能当", "不等于", "仅供参考", "未校准", "单 fold", "单折", "不是概率", "非概率",
          "不代表", "不能据此", "不足以", "需谨慎", "要谨慎", "不是个人情绪事实", "情绪事实", "无法可靠", "不宜过度",
          "只能作", "并不意味", "无法判断", "无法确定", "不能确认", "未经验证", "不能直接",
          "重要限定", "必须强调", "两点提醒", "两点说明", "注意这不是", "不是经过验证", "非验证"]


def hedge_stats(answers: list[str]) -> dict:
    text = "".join(answers)
    hits = {phrase: text.count(phrase) for phrase in HEDGES if phrase in text}
    total = sum(hits.values())
    return {"answer_chars": len(text), "hedge_hits": total, "hedges_per_1k_chars": round(1000 * total / max(1, len(text)), 2),
            "top_hedges": dict(sorted(hits.items(), key=lambda item: -item[1])[:8])}


# ---------------------------------------------------------------- fixtures

GENEVA = ["Fp1", "AF3", "F3", "F7", "FC5", "FC1", "C3", "T7", "CP5", "CP1", "P3", "P7", "PO3", "O1", "Oz", "Pz",
          "Fp2", "AF4", "Fz", "F4", "F8", "FC6", "FC2", "Cz", "C4", "T8", "CP6", "CP2", "P4", "P8", "PO4", "O2"]


def _alpha_burst(n_ch: int, sr: int, secs: float, channels: list[int], seed: int, amp=30.0, window=(20, 40)):
    rng = np.random.default_rng(seed)
    t = np.arange(int(sr * secs)) / sr
    x = rng.normal(0, 5.0, (n_ch, len(t)))
    burst = (t >= window[0]) & (t < window[1])
    for ch in channels:
        x[ch] += amp * np.sin(2 * np.pi * 10 * t) * burst
    return t, x


def eeg_npy_named(out: Path):
    _, x = _alpha_burst(8, 250, 60, [6, 7], 21)
    path = out / "subject01_eeg.npy"
    np.save(path, x)
    return path, {"sample_rate_hz": "250", "channel_names": "Fp1,Fp2,C3,C4,P7,P8,O1,O2"}


def labels_csv(out: Path):
    path = out / "self_report.csv"
    path.write_text("trial,valence,arousal,dominance\n1,7.5,2.5,6.0\n", encoding="utf-8")
    return path, {}


def eeg_npy_bare(out: Path):
    _, x = _alpha_burst(8, 250, 60, [6, 7], 22)
    path = out / "cyton_recording.npy"
    np.save(path, x)
    return path, {}


def protocol_txt(out: Path):
    path = out / "采集说明.txt"
    path.write_text("采集说明\n设备：OpenBCI Cyton，8 通道\n采样率：250 Hz\n通道顺序：Fp1 Fp2 C3 C4 P7 P8 O1 O2\n"
                    "单位：µV\n任务：静息，20–40 秒闭眼\n", encoding="utf-8")
    return path, {}


def chinese_csv(out: Path):
    t, x = _alpha_burst(8, 250, 60, [6, 7], 23)
    names = ["Fp1", "Fp2", "C3", "C4", "P7", "P8", "O1", "O2"]
    path = out / "被试02_静息.csv"
    with path.open("w", encoding="utf-8") as stream:
        stream.write("# 被试 02 静息记录，单位 µV\n")
        stream.write("time," + ",".join(names) + "\n")
        for i in range(len(t)):
            stream.write(f"{t[i]:.4f}," + ",".join(f"{v:.3f}" for v in x[:, i]) + "\n")
    return path, {}


def deap_dat(out: Path):
    sr, secs = 128, 63
    rng = np.random.default_rng(24)
    t = np.arange(sr * secs) / sr
    data = rng.normal(0, 4.0, (2, 40, len(t)))
    for ch in (GENEVA.index("O1"), GENEVA.index("Oz"), GENEVA.index("O2")):
        data[:, ch] += 25 * np.sin(2 * np.pi * 10 * t)
    labels = np.array([[7.1, 3.2, 5.0, 6.0], [2.5, 7.4, 4.0, 3.0]])
    path = out / "s99.dat"
    with path.open("wb") as stream:
        pickle.dump({"data": data, "labels": labels}, stream)
    return path, {}


def faa_npy(out: Path):
    sr = 250
    rng = np.random.default_rng(25)
    t = np.arange(sr * 60) / sr
    names = ["F3", "F4", "C3", "C4", "P3", "P4", "O1", "O2"]
    x = rng.normal(0, 3.0, (8, len(t)))
    alpha = np.sin(2 * np.pi * 10 * t)
    for name, amp in {"F3": 20, "F4": 10, "C3": 8, "C4": 8, "P3": 12, "P4": 12, "O1": 15, "O2": 15}.items():
        x[names.index(name)] += amp * alpha
    path = out / "frontal_rest.npy"
    np.save(path, x)
    return path, {"sample_rate_hz": str(sr), "channel_names": ",".join(names)}


FILES = {"eeg_npy_named": eeg_npy_named, "labels_csv": labels_csv, "eeg_npy_bare": eeg_npy_bare,
         "protocol_txt": protocol_txt, "chinese_csv": chinese_csv, "deap_dat": deap_dat, "faa_npy": faa_npy}


# ---------------------------------------------------------------- run

def run_probe(gw: str, probe: dict, fixture_dir: Path) -> dict:
    session = requests.post(f"{gw}/sessions", timeout=30).json()["session_id"]
    group = "upl_" + uuid.uuid4().hex
    uploads, attachment_ids = [], []
    for name in probe["files"]:
        path, form = FILES[name](fixture_dir)
        with path.open("rb") as stream:
            response = requests.post(f"{gw}/sessions/{session}/attachments", files={"file": (path.name, stream)},
                                     data={**form, "staged": "true", "upload_group_id": group}, timeout=300)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        manifest = body.get("manifest") or {}
        if manifest.get("manifest_id"):
            attachment_ids.append(manifest["manifest_id"])
        uploads.append({"file": path.name, "status": response.status_code, "modality": manifest.get("modality"),
                        "manifest_id": manifest.get("manifest_id"),
                        "error": None if response.ok else json.dumps(body, ensure_ascii=False)[:300]})
    started = time.time()
    events, answers = [], []
    for index, text in enumerate(probe["turns"]):
        payload = {"text": text, "mode": "readonly", "attachments": attachment_ids if index == 0 else []}
        response = requests.post(f"{gw}/sessions/{session}/messages", json=payload, stream=True, timeout=900)
        response.raise_for_status()
        answer = ""
        for event in sse(response):
            events.append(event)
            if event["type"] == "message_delta":
                answer += event.get("text", "")
            if event["type"] == "message_started":
                answer = ""
        answers.append(answer)
    final = answers[-1] if answers else ""
    called = [event["tool_name"] for event in events if event["type"] == "tool_execution_start"]
    tool_errors = [event["tool_name"] for event in events if event["type"] == "tool_execution_end" and event.get("is_error")]
    asked = sum(event["type"] == "user_input_requested" for event in events)
    statuses = []
    for event in events:
        if event["type"] == "tool_execution_start" and event.get("tool_name") == "save_emotion_assessment":
            args = event.get("args") or {}
            statuses.append({"status": args.get("assessment_status"), "label": args.get("label")})
    expect = probe.get("expect", {})
    low = final.lower()
    mention_hits = [any(word.lower() in low for word in group) for group in expect.get("must_mention", [])]
    bad_claims = [word for word in expect.get("must_not_claim", []) if word.lower() in low]
    modality_expect = expect.get("upload_modality", {})
    upload_ok = all(row["status"] == 200 for row in uploads) and all(
        next((row["modality"] for row in uploads if row["file"].startswith(prefix)), None) in allowed
        for prefix, allowed in modality_expect.items())
    errors = [event.get("text") for event in events if event["type"] == "error"]
    return {"probe": probe["id"], "session": session, "uploads": uploads, "upload_ok": upload_ok,
            "asked_user": asked, "max_asks": expect.get("max_asks"), "asks_ok": None if expect.get("max_asks") is None else asked <= expect["max_asks"],
            "mention_ok": all(mention_hits) and not bad_claims, "mention_hits": mention_hits, "bad_claims": bad_claims,
            "assessments": statuses, "called": called, "tool_errors": tool_errors, "errors": errors,
            "seconds": round(time.time() - started, 1), "answer": final, **hedge_stats([final])}


def summarize(rows: list[dict]) -> dict:
    def rate(key):
        values = [row[key] for row in rows if row.get(key) is not None]
        return round(sum(values) / len(values), 3) if values else None
    by_probe = {}
    for row in rows:
        by_probe.setdefault(row["probe"], []).append(row)
    return {"n": len(rows), "upload_ok": rate("upload_ok"), "asks_ok": rate("asks_ok"), "mention_ok": rate("mention_ok"),
            "user_input_requests": sum(row["asked_user"] for row in rows),
            "tool_errors": sum(len(row["tool_errors"]) for row in rows),
            "answer_style": hedge_stats([row["answer"] for row in rows]),
            "by_probe": {name: {"upload_ok": [r["upload_ok"] for r in items], "asked_user": [r["asked_user"] for r in items],
                                "mention_ok": [r["mention_ok"] for r in items],
                                "hedges_per_1k_chars": [r["hedges_per_1k_chars"] for r in items]}
                         for name, items in by_probe.items()}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://localhost:8080")
    parser.add_argument("--probes", default=str(Path(__file__).with_name("probes.yaml")))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", default="")
    parser.add_argument("--out", default=str(Path(__file__).with_name("out") / "probes"))
    parser.add_argument("--score", default="", help="answer-style metrics for an existing results.jsonl")
    args = parser.parse_args()
    if args.score:
        rows = [json.loads(line) for line in Path(args.score).read_text(encoding="utf-8").splitlines() if line.strip()]
        print(json.dumps(hedge_stats([row.get("answer", "") for row in rows]), ensure_ascii=False, indent=2))
        return
    out = Path(args.out)
    fixture_dir = out / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    model = requests.get(f"{args.gateway}/health", timeout=10).json().get("model", "?")
    probes = [probe for probe in yaml.safe_load(Path(args.probes).read_text(encoding="utf-8")) if probe["id"].startswith(args.only)]
    rows = []
    with (out / "results.jsonl").open("a", encoding="utf-8") as stream:
        for probe in probes:
            for _ in range(args.runs):
                try:
                    row = run_probe(args.gateway, probe, fixture_dir)
                except Exception as exc:  # noqa: BLE001
                    row = {"probe": probe["id"], "upload_ok": False, "asked_user": 0, "asks_ok": None, "mention_ok": False,
                           "tool_errors": [], "answer": "", "error": str(exc), **hedge_stats([""])}
                    print(f"[{probe['id']}] ERROR {exc}", file=sys.stderr)
                row["model"] = model
                rows.append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"[{probe['id']}] upload_ok={row['upload_ok']} asked={row['asked_user']} mention_ok={row['mention_ok']} "
                      f"hedges/1k={row['hedges_per_1k_chars']}", flush=True)
    summary = summarize(rows)
    summary["model"] = model
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "by_probe"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
