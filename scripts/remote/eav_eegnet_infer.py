#!/usr/bin/env python3
"""One SSH-owned EAV EEGNet inference. Reads and writes NPZ, never modifies weights."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import torch


def load_source(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare(signal: np.ndarray, metadata: dict, recipe: dict) -> tuple[np.ndarray, dict]:
    """Validate the cross-machine input once, then apply the original cache recipe."""
    if metadata["model_id"] != recipe["model_id"]:
        raise ValueError("Request model_id differs from deployed recipe")
    if metadata["channel_names"] != recipe["channel_names"]:
        raise ValueError("Request must use the recorded 30-electrode EAV order")
    if signal.ndim != 2 or signal.shape[0] != 30 or not np.isfinite(signal).all():
        raise ValueError("Expected finite [30, samples] signal")
    stage = metadata["input_stage"]
    rate = metadata["sample_rate_hz"]
    windows = metadata["windows"]
    if not windows:
        raise ValueError("No inference windows requested")
    if stage == "raw_eav_trial":
        if rate != 500 or metadata["unit"] != "dataset_native" or metadata["task_mode"] != "speaking" or signal.shape[1] % 10000:
            raise ValueError("Raw EAV input needs complete 20-second 500-Hz speaking trials in original numerical scale")
        builder = load_source("eegagent_eav_cache", recipe["preprocessing_source"])
        batches = []
        for start in range(0, signal.shape[1], 10000):
            trial = builder.preprocess_trial(
                np.asarray(signal[:, start:start + 10000], dtype=np.float64),
                orig_fs=500, target_fs=128, notch_hz=50.0, notch_q=30.0,
                band_low=0.5, band_high=45.0, clip_mad_k=7.0,
            )
            batches.append(builder.raw_5s_windows(trial, fs=128))
        prepared = np.concatenate(batches, axis=0)
        window_samples = 2500
        processing = recipe["input_recipe"]["preprocessing"]
    elif stage == "eav_preprocessed_128hz":
        # Benchmark/cache input is explicit; the product does not infer this stage from sampling rate.
        if rate != 128 or signal.shape[1] % 640:
            raise ValueError("Explicit EAV cache input requires full 640-sample windows at 128 Hz")
        prepared = signal.reshape(30, -1, 640).transpose(1, 0, 2).astype(np.float32)
        window_samples = 640
        processing = ["EAV preprocessing supplied by recorded cache source", recipe["input_recipe"]["preprocessing"][-1]]
    else:
        raise ValueError(f"Unsupported input stage: {stage}")
    indices = []
    for window in windows:
        start, end = window["start_sample"], window["end_sample"]
        if start % window_samples or end - start != window_samples or start < 0 or end > signal.shape[1]:
            raise ValueError("Each requested window must align with a complete non-overlapping 5-second window")
        indices.append(start // window_samples)
    selected = prepared[indices]
    # Exactly the trainer's normalize_sample: float32 reduction, global per-window C*T scope.
    normalized = np.stack([
        ((x - float(np.mean(x))) / (float(np.std(x)) + 1e-6)).astype(np.float32, copy=False)
        for x in selected
    ])[:, None, :, :]
    return normalized, {
        "input_stage": stage, "source_sample_rate_hz": rate, "model_sample_rate_hz": 128,
        "input_numeric_unit": metadata["unit"], "physical_voltage_unit": "unverified",
        "preprocessing_scope": "20-second trials" if stage == "raw_eav_trial" else "provided EAV cache",
        "steps": processing, "window_indices": indices,
        "tensor_shape": list(normalized.shape), "input_dtype": str(signal.dtype),
        "output_dtype": str(normalized.dtype), "windows": windows,
    }


def infer(request: Path, response: Path, recipe: dict) -> dict:
    with np.load(request, allow_pickle=False) as archive:
        signal = archive["signal"]
        metadata = json.loads(str(archive["metadata"].item()))
    inputs, preparation = prepare(signal, metadata, recipe)
    actual_hash = hashlib.sha256(Path(recipe["checkpoint"]).read_bytes()).hexdigest()
    if actual_hash != recipe["weight_sha256"]:
        raise ValueError("Checkpoint no longer matches the registered weight hash")
    source = load_source("eegagent_original_eegnet", recipe["architecture_source"])
    model = source.EEGNet(**recipe["architecture"])
    checkpoint = torch.load(recipe["checkpoint"], map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model"])
    model = model.to("cuda:0").eval()
    with torch.inference_mode():
        logits = model(torch.from_numpy(inputs).to("cuda:0")).cpu().numpy()
    result = {
        "model": {key: recipe[key] for key in ("model_id", "model_version", "task", "weight_sha256", "input_recipe")},
        "output": {"kind": "classification", "labels": recipe["labels"], "score_kind": "logits", "calibration": {"status": "unknown"}},
        "input_recipe": recipe["input_recipe"], "preparation": preparation,
        "runtime": {"torch": torch.__version__, "cuda": torch.version.cuda, "device": torch.cuda.get_device_name(0)},
    }
    np.savez_compressed(response, inputs=inputs, logits=logits, metadata=json.dumps(result, ensure_ascii=False))
    return {"status": "ok", "model_id": recipe["model_id"], "prediction_count": len(logits),
            "predicted_labels": [recipe["labels"][i] for i in logits.argmax(axis=1)], "response": str(response)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", type=Path, default=Path(__file__).with_name("eav_eegnet_recipe.json"))
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--witness", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(2)
    if args.witness:
        torch.manual_seed(20261012)
        x = torch.randn(8, 8, device="cuda:0")
        y = x @ x.T
        torch.cuda.synchronize()
        print("WITNESS", json.dumps({"shape": list(y.shape), "finite": bool(torch.isfinite(y).all()),
                                      "device": torch.cuda.get_device_name(0), "checksum": float(y.sum())}))
    else:
        if args.request is None or args.response is None:
            parser.error("--request and --response are required for inference")
        print(json.dumps(infer(args.request, args.response, json.loads(args.recipe.read_text())), ensure_ascii=False))


if __name__ == "__main__":
    main()
