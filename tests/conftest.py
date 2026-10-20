"""Portable model contracts for tests that mock the remote executor."""
from pathlib import Path
import json

import pytest
import yaml


@pytest.fixture
def commit_session():
    """Write the committed input/product snapshot normally published by the gateway."""
    from eegagent.worker import store

    def commit(sid, manifests=(), artifacts=(), workspace_id=None):
        path = store.ensure_layout() / "sessions" / (sid + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"manifests": list(manifests), "artifacts": list(artifacts),
                                    "workspace_id": workspace_id}), encoding="utf-8")
    return commit


@pytest.fixture
def registered_model_registry(tmp_path, monkeypatch):
    from eegagent.worker.models import EXECUTABLE_MODELS
    from eegagent.worker.registry import load_registry

    source = Path(__file__).parents[1] / "configs/model_registry.yaml"
    registry = yaml.safe_load(source.read_text(encoding="utf-8"))
    for model_id in EXECUTABLE_MODELS:
        model = registry["models"][model_id]
        model.update(status="registered", file_uri="mock://weights/test.pt", sha256="a" * 64)
    target = tmp_path / "test-registry.yaml"
    target.write_text(yaml.safe_dump(registry, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("EEGAGENT_MODEL_REGISTRY", str(target))
    load_registry.cache_clear()
    yield registry["models"]
    load_registry.cache_clear()
