#!/usr/bin/env python3
"""Execute the actual Full-CAMER-Photo-Fixed checkpoint on original EEG + video."""
from __future__ import annotations

import argparse
import ast
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoImageProcessor, SwinModel

from eav_eegnet_infer import load_source
from video_contract import probe_video


def load_model(recipe: dict):
    # Compile the original definitions unchanged, excluding its training/config/import side effects.
    names = {"laplacian", "B1ReLU", "GraphConv", "StructureGCBNet", "BiModalCrossAttention", "MultiModalGCBNetClassifier"}
    tree = ast.parse(Path(recipe["architecture_source"]).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    processor = AutoImageProcessor.from_pretrained(recipe["backbone_path"], local_files_only=True)
    processor.do_rescale = False
    backbone = SwinModel.from_pretrained(recipe["backbone_path"], local_files_only=True)
    scope = {"torch": torch, "nn": nn, "F": F, "os": os,
             "swin_processor": processor, "swin_model": backbone}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), recipe["architecture_source"], "exec"), scope)
    model = scope["MultiModalGCBNetClassifier"](**recipe["architecture"])
    if hashlib.sha256(Path(recipe["checkpoint"]).read_bytes()).hexdigest() != recipe["weight_sha256"]:
        raise ValueError("CAMER checkpoint differs from registered weight hash")
    model.load_state_dict(torch.load(recipe["checkpoint"], map_location="cpu", weights_only=True))
    return model.eval()


def prepare_eeg(signal: np.ndarray, metadata: dict, recipe: dict):
    if (metadata["model_id"] != recipe["model_id"] or metadata["sample_rate_hz"] != 500 or
        metadata["channel_names"] != recipe["channel_names"] or metadata["unit"] != "dataset_native" or
        metadata["input_stage"] != "raw_eav_trial" or metadata["task_mode"] != "speaking"):
        raise ValueError("CAMER requires the recorded EAV500Hz/30-electrode/native/speaking raw contract")
    if signal.ndim != 2 or signal.shape[0] != 30 or signal.shape[1] % 10000 or not np.isfinite(signal).all():
        raise ValueError("CAMER EEG input requires finite complete 20-second trials")
    builder = load_source("camer_eav_original_cache", recipe["preprocessing_source"])
    batches = []
    for start in range(0, signal.shape[1], 10000):
        trial = builder.preprocess_trial(np.asarray(signal[:, start:start + 10000], dtype=np.float64),
            orig_fs=500, target_fs=128, notch_hz=50., notch_q=30., band_low=.5, band_high=45., clip_mad_k=7.)
        de = builder.de_extraction_1s(trial, fs=128)
        features = builder.aggregate_5s_from_1s(builder.lds_smooth(de))
        batches.append(np.pad(features, ((0, 0), (0, 2), (0, 0))))
    all_features = np.concatenate(batches).astype(np.float32)
    indices = []
    for window in metadata["windows"]:
        start, end = window["start_sample"], window["end_sample"]
        if start % 2500 or end - start != 2500 or start < 0 or end > signal.shape[1]:
            raise ValueError("CAMER windows must be full 5-second windows aligned within original 20-second trials")
        indices.append(start // 2500)
    return all_features[indices]


def prepare_video(video: Path, windows: list[dict]):
    video_info = probe_video(video, require_cfr=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError("Cannot open uploaded participant video")
    fps = cap.get(cv2.CAP_PROP_FPS)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if count != video_info["timing"]["n_frames"] or not np.isclose(fps,video_info["fps"],rtol=1e-9,atol=0):
        cap.release()
        raise ValueError("OpenCV native frame clock differs from verified video timing")
    duration = count / fps
    images, timestamps, frame_indices = [], [], []
    try:
        for window in windows:
            t0, t1 = window["video_start_s"], window["video_end_s"]
            if t0 < 0 or t1 > duration + 1e-6 or t1 <= t0:
                raise ValueError(f"Video does not cover requested original-EEG window: {t0}..{t1}s")
            candidates = np.arange(int(np.ceil(t0 * 4)), int(np.ceil(t1 * 4))) * 250
            if len(candidates) < 6:
                raise ValueError("Not enough real video frames for six-frame CAMER input")
            selected = candidates[np.linspace(0, len(candidates) - 1, 6, dtype=int)]
            # OpenCV's historical POS_MSEC seek rounds positive frame positions
            # half up (.75s at 30fps -> frame 23), rather than Python's ties-even.
            indices = [int(float(ms) * fps / 1000 + .5) for ms in selected]
            if len(set(indices)) != 6 or any(not t0 <= index/fps < t1 for index in indices):
                raise ValueError("CAMER requires six distinct real source frames within each requested window")
            frames = []
            for ms, index in zip(selected, indices):
                cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = cap.read()
                if not ok:
                    raise ValueError(f"Uploaded video frame at {ms}ms could not be decoded")
                ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                if not ok:
                    raise ValueError("Original JPEG95 frame preparation failed")
                with Image.open(BytesIO(jpeg.tobytes())) as image:
                    frames.append(np.asarray(image.convert("RGB").resize((224, 224)), dtype=np.float32) / 255.)
            images.append(np.stack(frames))
            timestamps.append([int(ms) for ms in selected])
            frame_indices.append(indices)
    finally:
        cap.release()
    return np.stack(images), {"video_duration_s": duration, "frame_times_ms": timestamps,
                              "source_fps": fps, "source_frame_indices": frame_indices,
                              "source_frame_times_ms": [[round(index/fps*1000, 6) for index in row] for row in frame_indices],
                              "timing": video_info["timing"], "video_time_origin": "first decoded frame",
                              "crop": "none; original training used full source frames", "jpeg_quality": 95,
                              "candidate_fps": 4, "frames_per_window": 6, "rgb_range": [0, 1],
                              "resize": "PIL RGB BICUBIC 224x224", "windows": windows}


def infer(request: Path, response: Path, video: Path, recipe: dict):
    with np.load(request, allow_pickle=False) as archive:
        signal = archive["signal"]
        metadata = json.loads(str(archive["metadata"].item()))
    eeg_inputs = prepare_eeg(signal, metadata, recipe)
    video_inputs, video_preparation = prepare_video(video, metadata["windows"])
    model = load_model(recipe).to("cuda:0")
    with torch.inference_mode():
        logits, _, _ = model(torch.from_numpy(eeg_inputs).to("cuda:0"), torch.from_numpy(video_inputs).to("cuda:0"))
        logits = logits.cpu().numpy()
    result = {
        "model": {key: recipe[key] for key in ("model_id", "model_version", "task", "weight_sha256", "input_recipe")},
        "output": {"kind": "classification", "labels": recipe["labels"], "score_kind": "logits", "calibration": {"status": "unknown"}},
        "input_recipe": recipe["input_recipe"],
        "preparation": {"input_stage": "raw_eav_trial", "input_dtype": str(signal.dtype),
                        "tensor_shape": list(eeg_inputs.shape), "unit": "dataset_native", "physical_voltage_unit": "unverified",
                        "steps": recipe["input_recipe"]["eeg_preprocessing"], "windows": metadata["windows"]},
        "video_preparation": {**video_preparation, "tensor_shape": list(video_inputs.shape),
                              "sync_offset_s": metadata["sync_offset_s"], "video_manifest_id": metadata["video_manifest_id"]},
        "runtime": {"torch": torch.__version__, "cuda": torch.version.cuda, "device": torch.cuda.get_device_name(0)},
    }
    np.savez_compressed(response, eeg_inputs=eeg_inputs, video_inputs=video_inputs, logits=logits,
                        metadata=json.dumps(result, ensure_ascii=False))
    return {"status": "ok", "model_id": recipe["model_id"], "prediction_count": len(logits),
            "predicted_labels": [recipe["labels"][i] for i in logits.argmax(axis=1)], "response": str(response)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, default=Path(__file__).with_name("camer_eav_recipe.json"))
    args = parser.parse_args()
    torch.set_num_threads(2)
    print(json.dumps(infer(args.request, args.response, args.video, json.loads(args.recipe.read_text())), ensure_ascii=False))


if __name__ == "__main__":
    main()
