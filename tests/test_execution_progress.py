from pathlib import Path
import time

from fastapi.testclient import TestClient

from eegagent.worker import app, remote_model, progress, script_jobs


def test_worker_streams_real_stages_before_the_terminal_result(monkeypatch):
    def tool(name, args):
        progress.report("upload_eeg", "上传 EEG 输入", "fixture")
        progress.report("inference", "远程推理中")
        return {"status":"ok", "summary":{"prediction_count":2}}
    monkeypatch.setattr(app, "run_tool", tool)
    with TestClient(app.app).stream("POST", "/tools/run_model/events", json={"args":{}}) as response:
        import json
        events = [json.loads(line) for line in response.iter_lines()]
    assert [event["type"] for event in events] == ["progress", "progress", "result"]
    assert [item["stage"] for item in events[-1]["result"]["execution_progress"]] == ["upload_eeg", "inference"]


def test_remote_transport_reports_each_stage_at_its_actual_command(monkeypatch, tmp_path):
    commands, stages = [], []
    request = tmp_path / "request.npz";request.write_bytes(b"fixture")
    video = tmp_path / "face.mp4";video.write_bytes(b"fixture")
    monkeypatch.setattr(remote_model, "_command", lambda command,*args: commands.append((list(command),[stage["stage"] for stage in stages])))
    model = {"modalities":["eeg","face_video"], "remote":{"root":"/models","host":"fixture","script":"infer.py","python":"python","gpu":0}, "paired_video":{"source_path":str(video)}}
    with progress.channel(stages.append):
        remote_model._remote_infer(request,tmp_path/"response.npz",model,"run_fixture",script_jobs.ScriptJob(),time.monotonic()+30,tmp_path/"log")
    assert [stage["stage"] for stage in stages] == ["upload_eeg","upload_video","inference","download"]
    assert commands[1][1][-1] == "upload_eeg"
    assert commands[2][1][-1] == "upload_video"
    assert commands[3][1][-1] == "inference"
    assert commands[4][1][-1] == "download"
