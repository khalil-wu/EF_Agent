"""eegagent.doctor: local setup diagnostic, no network calls."""

import importlib.util
import json

import pytest

from eegagent import doctor


def test_collect_checks_shape_and_llm_warning(monkeypatch):
    for name in ("EEGAGENT_LLM_BASE_URL", "EEGAGENT_LLM_MODEL", "EFAGENT_LLM_BASE_URL", "EFAGENT_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    checks = doctor.collect_checks()
    assert all(set(c) == {"name", "status", "detail", "remedy"} for c in checks)
    assert {c["status"] for c in checks} <= {"ok", "warning", "error"}
    llm = next(c for c in checks if c["name"] == "LLM configuration")
    assert llm["status"] == "warning" and "EEGAGENT_LLM_BASE_URL" in llm["detail"]
    embed = next(c for c in checks if c["name"] == "remote embeddings")
    assert embed["status"] == "ok" and "disabled" in embed["detail"]


def test_missing_module_is_error_and_missing_ffmpeg_is_warning(monkeypatch):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n: None if n == "mne" else real_find_spec(n))
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None if n in ("ffmpeg", "ffprobe") else "/usr/bin/x")
    checks = {c["name"]: c for c in doctor.collect_checks()}
    assert checks["python module mne"]["status"] == "error"
    assert checks["ffmpeg executable"]["status"] == "warning"
    assert checks["go executable"]["status"] == "ok"


def test_alias_env_overrides_and_partial_embed_warns(monkeypatch):
    monkeypatch.setenv("EEGAGENT_LLM_BASE_URL", "")           # alias overrides, not setdefault
    monkeypatch.setenv("EEGAGENT_LLM_MODEL", "stale-model-marker")
    monkeypatch.setenv("EFAGENT_LLM_BASE_URL", "http://secret-host.internal:9/v1")
    monkeypatch.setenv("EFAGENT_LLM_MODEL", "secret-model-marker")
    monkeypatch.setenv("EEGAGENT_EMBED_PROVIDER", "openai")
    monkeypatch.delenv("EEGAGENT_EMBED_BASE_URL", raising=False)
    monkeypatch.delenv("EEGAGENT_EMBED_MODEL", raising=False)
    checks = {c["name"]: c for c in doctor.collect_checks()}
    assert checks["LLM configuration"]["status"] == "ok"
    assert checks["remote embeddings"]["status"] == "warning"


@pytest.mark.parametrize("provider,base,model,expected", [
    (None, "http://x/v1", "embed-m", "ok"),        # base URL alone auto-selects openai
    (None, "http://x/v1", None, "warning"),        # partial: base without model
    ("none", "http://x/v1", "embed-m", "ok"),      # explicit none stays disabled
])
def test_embed_provider_autodetect_and_explicit_none(monkeypatch, provider, base, model, expected):
    for name in ("EEGAGENT_EMBED_PROVIDER", "EEGAGENT_EMBED_BASE_URL", "EEGAGENT_EMBED_MODEL"):
        monkeypatch.delenv(name, raising=False)
    if provider is not None:
        monkeypatch.setenv("EEGAGENT_EMBED_PROVIDER", provider)
    if base is not None:
        monkeypatch.setenv("EEGAGENT_EMBED_BASE_URL", base)
    if model is not None:
        monkeypatch.setenv("EEGAGENT_EMBED_MODEL", model)
    embed = next(c for c in doctor.collect_checks() if c["name"] == "remote embeddings")
    assert embed["status"] == expected


def test_cli_json_is_valid_and_never_prints_values(monkeypatch, capsys):
    markers = {"EFAGENT_LLM_BASE_URL": "http://secret-host.internal:9/v1",
               "EFAGENT_LLM_MODEL": "secret-model-marker",
               "EEGAGENT_LLM_API_KEY": "sk-fake-marker-not-real",
               "EFAGENT_EMBED_API_KEY": "mk-fake-marker-not-real"}
    for name, value in markers.items():
        monkeypatch.setenv(name, value)
    assert doctor.main(["--json"]) == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert all(c["status"] in ("ok", "warning", "error") for c in parsed)
    for value in markers.values():
        assert value not in out


def test_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.delenv("EEGAGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("EFAGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("EEGAGENT_LLM_MODEL", raising=False)
    assert doctor.main(["--json"]) == 0          # warning only
    assert doctor.main(["--json", "--strict"]) == 1
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n: None if n == "mne" else real_find_spec(n))
    assert doctor.main(["--json"]) == 1          # error
    capsys.readouterr()


def test_broken_module_probe_does_not_crash(monkeypatch):
    def boom(_name):
        raise RuntimeError("corrupt site-packages")
    monkeypatch.setattr(importlib.util, "find_spec", boom)
    checks = {c["name"]: c["status"] for c in doctor.collect_checks()}
    assert checks["python module numpy"] == "error"
