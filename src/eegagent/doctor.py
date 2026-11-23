"""EFAgent setup diagnostic: local checks only, no network calls.

`python -m eegagent.doctor [--json] [--strict]` prints each check's
name/status/detail/remedy and exits 1 on any error (or on warnings too under
--strict). Check details carry names, versions and remediation hints only —
never environment values, credentials or private paths.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from importlib import metadata

WORKER_MODULES = ("numpy", "scipy", "fastapi", "uvicorn", "mne", "pymupdf")
RUNTIME_TOOLS = ("ffmpeg", "ffprobe")
BUILD_TOOLS = ("go", "node", "npm")


def _env() -> dict[str, str]:
    """EEGAGENT_ values with EFAGENT_ aliases applied, as a private copy.

    Matches the launchers: an EFAGENT_ alias overrides the same-named
    EEGAGENT_ variable.
    """
    env = dict(os.environ)
    for key, value in os.environ.items():
        if key.startswith("EFAGENT_"):
            env["EEGAGENT_" + key[8:]] = value
    return env


def _module_check(name: str) -> dict:
    try:
        found = importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001 - a broken install must not crash the doctor
        found = False
    if not found:
        return {"name": f"python module {name}", "status": "error", "detail": "not importable",
                "remedy": "python -m pip install -e \".[worker,toolbox,knowledge,test]\""}
    try:
        detail = f"installed ({metadata.version(name)})"
    except metadata.PackageNotFoundError:
        detail = "installed"
    return {"name": f"python module {name}", "status": "ok", "detail": detail, "remedy": ""}


def _tool_check(name: str, remedy: str) -> dict:
    path = shutil.which(name)
    if path:
        return {"name": f"{name} executable", "status": "ok", "detail": "found on PATH", "remedy": ""}
    return {"name": f"{name} executable", "status": "warning", "detail": "not on PATH", "remedy": remedy}


def _tesseract_check() -> list[dict]:
    exe = shutil.which("tesseract")
    if not exe:
        return [{"name": "tesseract", "status": "warning", "detail": "not on PATH; scanned-PDF OCR disabled",
                 "remedy": "macOS: brew install tesseract; Windows: install Tesseract and set TESSDATA_PREFIX"}]
    checks = [{"name": "tesseract", "status": "ok", "detail": "found on PATH", "remedy": ""}]
    try:
        langs = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=15).stdout
    except Exception:  # noqa: BLE001
        langs = ""
    for lang in ("eng", "chi_sim"):
        if lang in langs:
            checks.append({"name": f"tesseract language {lang}", "status": "ok", "detail": "installed", "remedy": ""})
        else:
            checks.append({"name": f"tesseract language {lang}", "status": "warning",
                           "detail": "language data missing",
                           "remedy": "add the traineddata file to the tessdata directory (chi_sim required for Chinese documents)"})
    return checks


def _provider_checks(env: dict[str, str]) -> list[dict]:
    missing_llm = [k for k in ("EEGAGENT_LLM_BASE_URL", "EEGAGENT_LLM_MODEL") if not env.get(k)]
    if missing_llm:
        llm = {"name": "LLM configuration", "status": "warning",
               "detail": "not set: " + ", ".join(missing_llm),
               "remedy": "set EFAGENT_LLM_BASE_URL/EFAGENT_LLM_MODEL in scripts/settings.local.sh"}
    else:
        llm = {"name": "LLM configuration", "status": "ok", "detail": "base URL and model configured", "remedy": ""}
    provider = (env.get("EEGAGENT_EMBED_PROVIDER", "").strip().lower()
                or ("openai" if env.get("EEGAGENT_EMBED_BASE_URL") else "none"))
    if provider in ("", "none"):
        embed = {"name": "remote embeddings", "status": "ok", "detail": "disabled (keyword retrieval only)", "remedy": ""}
    else:
        missing = [k for k in ("EEGAGENT_EMBED_BASE_URL", "EEGAGENT_EMBED_MODEL") if not env.get(k)]
        embed = {"name": "remote embeddings", "status": "warning" if missing else "ok",
                 "detail": ("not set: " + ", ".join(missing)) if missing else "endpoint and model configured",
                 "remedy": "set EFAGENT_EMBED_BASE_URL/EFAGENT_EMBED_MODEL" if missing else ""}
    return [llm, embed]


def collect_checks() -> list[dict]:
    checks = []
    vi = sys.version_info
    checks.append({"name": "python", "status": "ok" if vi >= (3, 11) else "error",
                   "detail": f"{vi.major}.{vi.minor}.{vi.micro}" + ("" if vi >= (3, 11) else " (>=3.11 required)"),
                   "remedy": "" if vi >= (3, 11) else "install Python 3.11 or newer"})
    checks += [_module_check(name) for name in WORKER_MODULES]
    for tool in BUILD_TOOLS:
        checks.append(_tool_check(tool, "required only to rebuild the gateway/frontend, not to run"))
    for tool in RUNTIME_TOOLS:
        checks.append(_tool_check(tool, "optional: install ffmpeg for audio/video attachment previews"))
    checks += _tesseract_check()
    checks += _provider_checks(_env())
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eegagent.doctor", description="EFAgent setup diagnostic (local checks only)")
    parser.add_argument("--json", action="store_true", help="emit check results as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings as well as errors")
    args = parser.parse_args(argv)
    checks = collect_checks()
    if args.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            line = f"{c['status']:>7}  {c['name']}: {c['detail']}"
            print(line + (f"  -> {c['remedy']}" if c["remedy"] else ""))
    statuses = {c["status"] for c in checks}
    return 1 if "error" in statuses or (args.strict and "warning" in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
