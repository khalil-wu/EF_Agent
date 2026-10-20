"""Subprocess entry for run_python. Installs an audit-hook sandbox, preloads `eeg`, executes the script.

Sandbox rules (best effort, process-local):
  - reads: only under READ_ROOTS (storage, data dirs, scratch), the Python installation and system fonts
  - direct writes: this session's scratch, artifacts dir, matplotlib cache and tmp
  - eeg.call: native local tools save manifests; file tools write the selected workspace
  - no sockets, no subprocesses, no os.system/exec/fork, no webbrowser
Usage: python sandbox_runner.py <config.json>
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

cfg = json.load(open(sys.argv[1], encoding="utf-8"))  # read before the hook is installed

SCRATCH = cfg["scratch"]
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ["MPLCONFIGDIR"] = os.path.join(SCRATCH, ".mpl")
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)
import tempfile  # noqa: E402

TMP = tempfile.gettempdir()


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p)))


def _under(p: str, roots) -> bool:
    for r in roots:
        r = _norm(r)
        if p == r or p.startswith(r.rstrip("\\/") + os.sep):
            return True
    return False


READ_ROOTS = [_norm(r) for r in cfg["read_roots"]]
WRITE_ROOTS = [_norm(r) for r in cfg["write_roots"]] + [_norm(os.environ["MPLCONFIGDIR"]), _norm(TMP)]
TOOL_WRITE_ROOTS: list[str] = []
LIB_ROOTS = {_norm(p) for p in [sys.prefix, sys.base_prefix, sys.exec_prefix, *sys.path, os.path.dirname(os.__file__), cfg["src_dir"]] if p}
for extra in ("C:\\Windows\\Fonts", "/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
              os.path.expanduser("~/AppData/Local/Microsoft/Windows/Fonts"),
              os.path.expanduser("~/.fonts"), os.path.expanduser("~/Library/Fonts")):
    if os.path.isdir(extra):
        LIB_ROOTS.add(_norm(extra))

# os.putenv/unsetenv are allowed: numpy/mne/matplotlib set env vars on import, and with subprocesses blocked an env
# change cannot leave this process. ctypes.dlopen is allowed too (`import ctypes` loads kernel32 on Windows, numpy
# imports ctypes); instead the process/network entry points are denied at symbol lookup (ctypes.dlsym).
BLOCKED_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty",
                  "os.startfile", "_winapi.CreateProcess", "webbrowser.open", "os.kill", "os.killpg", "signal.pthread_kill"}
DENIED_SYMBOLS = {"createprocessa", "createprocessw", "createprocessasusera", "createprocessasuserw", "createprocesswithlogonw",
                  "winexec", "shellexecutea", "shellexecutew", "shellexecuteexa", "shellexecuteexw", "system", "_wsystem",
                  "popen", "_popen", "_wpopen", "execv", "execve", "execvp", "execvpe", "execl", "execlp", "execle", "fork",
                  "vfork", "posix_spawn", "posix_spawnp", "socket", "wsasocketa", "wsasocketw", "connect", "wsaconnect",
                  "urldownloadtofilea", "urldownloadtofilew", "internetopena", "internetopenw", "winhttpopen"}
PATH_EVENTS = {"os.remove", "os.rename", "os.rmdir", "os.truncate", "shutil.rmtree", "shutil.move", "os.chmod", "os.chflags",
               "os.link", "os.symlink", "os.mkdir", "os.utime"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _hook(event: str, args):
    if event == "open":
        path, mode, flags = args
        if path is None or isinstance(path, int):
            return
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, str):
            return
        if path == os.devnull:
            return
        p = _norm(path)
        writing = bool(mode and any(c in mode for c in "wax+")) or bool(flags and (flags & WRITE_FLAGS))
        if writing:
            if not _under(p, WRITE_ROOTS + TOOL_WRITE_ROOTS):
                raise PermissionError(f"sandbox: 只能写入本会话的 scratch 或通过 eeg.save_* 保存产物，拒绝写 {path}")
        elif not (_under(p, READ_ROOTS) or _under(p, WRITE_ROOTS) or _under(p, LIB_ROOTS)):
            raise PermissionError(f"sandbox: 路径不在允许的数据目录内，拒绝读取 {path}")
    elif event in BLOCKED_EVENTS or event.startswith("socket.") or event.startswith("subprocess."):
        raise PermissionError(f"sandbox: {event} 不允许（脚本内不能联网、不能起子进程）")
    elif event in ("ctypes.dlsym", "ctypes.dlsym/handle"):
        name = args[1] if len(args) > 1 else None
        if isinstance(name, str) and name.lower() in DENIED_SYMBOLS:
            raise PermissionError(f"sandbox: ctypes 调用 {name} 不允许（脚本内不能联网、不能起子进程）")
    elif event in PATH_EVENTS:
        for a in args:
            if isinstance(a, (str, bytes)):
                a = os.fsdecode(a) if isinstance(a, bytes) else a
                if not _under(_norm(a), WRITE_ROOTS + TOOL_WRITE_ROOTS):
                    raise PermissionError(f"sandbox: {event} 只允许在 scratch 内：{a}")


sys.addaudithook(_hook)


def _call_local_tool(name, arguments):
    """Native tools persist their own manifests; ordinary script writes keep the existing roots."""
    global TOOL_WRITE_ROOTS
    from eegagent.worker.tools import run_tool
    TOOL_WRITE_ROOTS = [_norm(root) for root in cfg["tool_write_roots"]]
    if name in {"write_file", "edit_file", "save_to_workspace"} and cfg.get("workspace_root"):
        TOOL_WRITE_ROOTS.append(_norm(cfg["workspace_root"]))
    try:
        return run_tool(name, arguments)
    finally:
        TOOL_WRITE_ROOTS = []


def _write_result(value):
    temporary = cfg["result_path"] + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, cfg["result_path"])


def _checkpoint():
    _write_result({"ok": False, "artifacts": api.created, "manifests": list(api.manifests.values()),
                   "workspace_files": list(api.workspace_files.values()),
                   "source_manifest_ids": sorted(api._manifests), "parent_artifact_ids": sorted(api._parents), "error": None})

# --------------------------------------------------------------- run

result: dict = {"ok": False, "artifacts": [], "error": None, "elapsed_s": 0.0}
t0 = time.time()
try:
    sys.path.insert(0, cfg["src_dir"])
    from eegagent.worker.sandbox_api import EEGApi
    from eegagent.worker.outputs import _mpl
    from eegagent.worker.progress import channel

    _mpl()  # Shared scientific style applies to native pyplot, MNE and eeg.figure.

    api = EEGApi(cfg["session_id"], SCRATCH, cfg["run_id"], cfg["code_sha"],
                 workspace_id=cfg.get("workspace_id"), call_tool=_call_local_tool, checkpoint=_checkpoint)
    code = open(cfg["code_path"], encoding="utf-8").read()
    g = {"__name__": "__main__", "__file__": cfg["code_path"], "eeg": api}
    with open(cfg["progress_path"], "a", encoding="utf-8", buffering=1) as events:
        with channel(lambda event: events.write(json.dumps(event, ensure_ascii=False) + "\n")):
            try:
                exec(compile(code, cfg["code_path"], "exec"), g)
                result["ok"] = True
            except SystemExit as exc:  # scripts may call sys.exit()
                result["ok"] = (exc.code in (None, 0))
                if not result["ok"]:
                    result["error"] = {"type": "SystemExit", "message": f"exit code {exc.code}"}
            except BaseException as exc:  # noqa: BLE001
                tb = traceback.format_exc().splitlines()
                # drop runner frames, keep the script's
                keep = [ln for ln in tb if "sandbox_runner.py" not in ln]
                result["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": "\n".join(keep[-25:])}
    result["artifacts"] = api.created
    result["manifests"] = list(api.manifests.values())
    result["workspace_files"] = list(api.workspace_files.values())
    result["source_manifest_ids"] = sorted(api._manifests)
    result["parent_artifact_ids"] = sorted(api._parents)
except BaseException as exc:  # noqa: BLE001
    result["error"] = {"type": type(exc).__name__, "message": f"runner: {exc}", "traceback": traceback.format_exc()[-2000:]}
finally:
    result["elapsed_s"] = round(time.time() - t0, 3)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    _write_result(result)
