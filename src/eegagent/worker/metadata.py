"""Apply user-confirmed recording metadata while preserving the original manifest."""

import uuid

from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.readers import inferred
from eegagent.worker.registry import check_model_compat
from eegagent.worker.store import load_manifest, save_manifest


def update_record_metadata(manifest_id: str, sample_rate_hz=None, channel_names=None, unit=None,
                           video_role=None, sync_offset_s=None, dataset=None, reference=None,
                           baseline_duration_s=None, label_names=None, task_mode=None, preprocessing=None,
                           trial_index=None) -> dict:
    original = load_manifest(manifest_id)
    if sample_rate_hz is not None and sample_rate_hz <= 0:
        raise ValueError("采样率必须大于 0")
    if unit is not None and unit not in ("V", "mV", "uV", "dataset_native", "a.u."):
        raise ValueError("单位需要是 V、mV、uV、dataset_native 或 a.u.")
    if task_mode is not None and task_mode not in ("speaking", "listening", "unknown"):
        raise ValueError("task_mode 需要是 speaking、listening 或 unknown")
    if preprocessing is not None and not isinstance(preprocessing, dict) and preprocessing not in ("deap_official_preprocessed", "eav_raw"):
        raise ValueError("preprocessing 采用已知数据集契约名，或明确的滤波、参考等处理事实对象")
    if original["modality"] == "eeg":
        if channel_names is not None and len(channel_names) != original["n_channels"]:
            raise ValueError(f"需要 {original['n_channels']} 个通道名")
        if label_names is not None and not original.get("labels"):
            raise ValueError("文件没有标签列，无需提供 label_names")
        if label_names is not None and len(label_names) != len(original["labels"]["values"]):
            raise ValueError("标签名称数量需与文件中的标签列数量一致")
        record = inspect_record(original["source_path"],
                                sample_rate_hz=sample_rate_hz if sample_rate_hz is not None else original["sample_rate_hz"],
                                channel_names=channel_names if channel_names is not None else original["channel_names"],
                                trial_index=trial_index if trial_index is not None else original.get("trial_index"))
        if unit is not None:
            record["unit"] = inferred(unit, "user_confirmed")
        else:
            record["unit"] = original["unit"]
        if channel_names is None:
            record["channel_layout"] = original["channel_layout"]
        if channel_names is not None and record["channel_layout"]["source"] != "unknown":
            record["channel_layout"]["source"] = "user_confirmed"
        evidence = {**record["metadata_evidence"], **original.get("metadata_evidence", {})}
        selecting_trial = trial_index is not None and trial_index != original.get("trial_index")
        if selecting_trial:
            for field in ("task_mode", "source_provenance"):
                if field in record["metadata_evidence"]:
                    evidence[field] = record["metadata_evidence"][field]
                else:
                    evidence.pop(field, None)
        # Re-reading unchanged values is not a new user confirmation, including legacy adapters.
        previous = original.get("metadata_evidence", {})
        legacy_format = any(original.get(key, {}).get("source") == "dataset_adapter"
                            for key in ("unit", "channel_layout"))
        for key in ("sample_rate_hz", "channel_names"):
            if key not in previous:
                source = "format_assumption" if legacy_format else "unknown"
                if key == "channel_names" and not legacy_format:
                    source = original["channel_layout"]["source"]
                evidence[key] = inferred(original[key], source)
        evidence["unit"] = record["unit"]
        for key, value in (("sample_rate_hz", sample_rate_hz), ("channel_names", channel_names), ("unit", unit),
                           ("reference", reference), ("baseline_duration_s", baseline_duration_s), ("label_names", label_names),
                           ("task_mode", task_mode), ("preprocessing", preprocessing)):
            if value is not None:
                evidence[key] = inferred(value, "user_confirmed")
        record["dataset"] = dataset if dataset is not None else original.get("dataset", "unknown")
        if dataset is not None:
            evidence["dataset"] = inferred(dataset, "user_declared")
        elif legacy_format:
            evidence["dataset"] = inferred(record["dataset"], "format_assumption")
        record["reference"] = reference if reference is not None else original.get("reference", "unknown")
        if isinstance(preprocessing, dict) and "reference" in preprocessing and reference is None:
            record["reference"] = preprocessing["reference"]
            evidence["reference"] = inferred(record["reference"], "user_confirmed")
        if not selecting_trial:
            record["labels"] = original.get("labels")
            record["trial_selection"] = original.get("trial_selection", record["trial_selection"])
        elif record["labels"] and (original.get("labels") or {}).get("names"):
            record["labels"]["names"] = original["labels"]["names"]
        if label_names is not None:
            values = record["labels"]["values"]
            record["labels"] = {"values": values, "names": list(label_names)}
        record["metadata_evidence"] = evidence
        if isinstance(preprocessing, str):
            from eegagent.worker.channel_orders import DEAP_MAHNOB_CHANNEL_ORDER, EAV_CHANNEL_ORDER
            is_deap = preprocessing == "deap_official_preprocessed"
            stage = "deap_preprocessed_trial" if is_deap else "raw_eav_trial"
            evidence["input_stage"] = inferred(stage, "user_confirmed")
            candidate = record.get("format_profile") == ("deap_preprocessed" if is_deap else "eav_raw_segments")
            # Only a confirmed native container convention supplies names/clock/scale.
            # A generic CSV/NPY/MAT must carry or receive its actual scientific metadata.
            if candidate:
                if sample_rate_hz is None and evidence["sample_rate_hz"]["source"] in ("unknown", "format_assumption"):
                    rate = 128.0 if is_deap else 500.0
                    record["sample_rate_hz"] = rate
                    record["duration_s"] = record["n_samples"] / rate
                    evidence["sample_rate_hz"] = inferred(rate, "user_confirmed")
                if channel_names is None and evidence["channel_names"]["source"] in ("generated", "unknown", "format_assumption"):
                    names = list(DEAP_MAHNOB_CHANNEL_ORDER if is_deap else EAV_CHANNEL_ORDER)
                    record["channel_names"] = names
                    record["channel_layout"] = inferred(dict(zip(names, names)), "user_confirmed")
                    evidence["channel_names"] = inferred(names, "user_confirmed")
                if unit is None and record["unit"]["source"] != "file_header":
                    record["unit"] = inferred("dataset_native", "user_confirmed")
                    evidence["unit"] = record["unit"]
            if reference is None and record["reference"] == "unknown":
                record["reference"] = "average" if is_deap else "mastoids"
                evidence["reference"] = inferred(record["reference"], "user_confirmed")
            if is_deap and baseline_duration_s is None and evidence.get("baseline_duration_s", {}).get("source") not in ("file_header", "user_confirmed"):
                evidence["baseline_duration_s"] = inferred(3.0, "user_confirmed")
            if dataset is None and record["dataset"] == "unknown":
                record["dataset"] = "DEAP" if is_deap else "EAV"
                evidence["dataset"] = inferred(record["dataset"], "user_declared")
        record["compat"] = check_model_compat(record)
    elif original["modality"] == "video":
        if video_role is not None and video_role not in ("face", "stimulus", "other"):
            raise ValueError("视频用途需要是 face、stimulus 或 other")
        record = {**original, "manifest_id": "vid_" + uuid.uuid4().hex[:8]}
        if not original.get("timing"):
            from eegagent.worker.attachments import _video_metadata
            from pathlib import Path
            facts, warnings = _video_metadata(Path(original["source_path"]))
            record.update(facts, warnings=warnings)
        if video_role is not None:
            record["role"] = inferred(video_role, "user_confirmed")
    else:
        raise ValueError("仅 EEG 和视频需要记录元数据")
    if sync_offset_s is not None:
        record["sync"] = {"clock": "eeg_original", "start_offset_s": sync_offset_s, "source": "user_confirmed"}
    else:
        record["sync"] = original.get("sync")
    record["original_name"] = original.get("original_name", original["source_path"].replace("\\", "/").split("/")[-1])
    if "upload_group_id" in original:
        record["upload_group_id"] = original["upload_group_id"]
    save_manifest(record)
    return {"status": "ok", "manifest": record, "previous_manifest_id": manifest_id}
