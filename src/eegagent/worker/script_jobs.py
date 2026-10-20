"""Own the one running script process per session so a user stop reaches Python."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from subprocess import Popen


@dataclass
class ScriptJob:
    cancelled: bool = False
    process: Popen | None = None
    done: Event = field(default_factory=Event)
    result: dict | None = None


lock = Lock()
active: dict[str, ScriptJob] = {}


def stop_script(session_id: str) -> dict:
    with lock:
        job = active.get(session_id)
        if job is None:
            return {"status": "idle"}
        job.cancelled = True
        if job.process is not None and job.process.poll() is None:
            job.process.terminate()
    finished = job.done.wait(5)
    response = {"status": "stopped" if finished else "stopping"}
    if job.result is not None:
        response["result"] = job.result
    return response
