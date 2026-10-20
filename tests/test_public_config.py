"""The default catalog exposes contracts without claiming installed weights."""
from pathlib import Path

import yaml

from eegagent.worker.models import list_models


def test_public_registry_requires_local_weight_registration():
    path = Path(__file__).parents[1] / "configs/model_registry.yaml"
    registry = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]
    rows = list_models(registry=registry)["models"]
    assert rows and not any(row["inference_available"] for row in rows)
    assert not any(model.get("sha256") for model in registry.values())
    assert all(not model.get("file_uri") or model["file_uri"].startswith("https://") for model in registry.values())
