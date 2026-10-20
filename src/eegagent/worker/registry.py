"""Model registry loader and deterministic compatibility check.

check_model_compat runs at upload time; the result goes into the manifest and then into
the prompt as file context. The LLM never has to remember to call it.
"""

from __future__ import annotations

import os
from collections import Counter
from functools import lru_cache
from pathlib import Path

from eegagent.worker.channels import TEN_TWENTY, normalize_channel  # noqa: F401  (re-exported)


def registry_path() -> Path:
    return Path(os.environ.get("EEGAGENT_MODEL_REGISTRY", Path(__file__).resolve().parents[3] / "configs" / "model_registry.yaml"))


@lru_cache(maxsize=1)
def load_registry() -> dict[str, dict]:
    import yaml  # optional dependency; only the worker needs it

    data = yaml.safe_load(registry_path().read_text(encoding="utf-8")) or {}
    return data.get("models", {})


def known_ten_twenty(channel_names: list[str]) -> list[str]:
    return [c for c in channel_names if normalize_channel(c) in TEN_TWENTY]


def _channel_order_names(model: dict) -> list[str] | None:
    """Expected channel names for a model, if the registry knows them."""
    order = model.get("input", {}).get("channel_order")
    if not order:
        return None
    try:
        from eegagent.worker.channel_orders import CHANNEL_ORDERS

        return CHANNEL_ORDERS.get(order)
    except ImportError:
        return None


def check_model_compat(manifest: dict, registry: dict[str, dict] | None = None) -> list[dict]:
    """Return one CompatResult per registry entry with a usable status."""
    registry = registry if registry is not None else load_registry()
    results: list[dict] = []
    modality = manifest.get("modality")
    chan_names = [normalize_channel(c) for c in manifest.get("channel_names", [])]
    chan_counts = Counter(chan_names)
    layout = manifest.get("channel_layout", {}) or {}
    layout_known = layout.get("source") in ("file_header", "user_confirmed")
    unit = manifest.get("unit") or {}
    unit_known = unit.get("source") in ("file_header", "user_confirmed") and unit.get("value") in ("V", "mV", "uV", "µV")
    sr = manifest.get("sample_rate_hz")
    sr_evidence = manifest.get("metadata_evidence", {}).get("sample_rate_hz", {})
    sr_assumed = sr_evidence.get("source") in ("format_assumption", "dataset_adapter", "unknown")
    if not sr_evidence and layout.get("source") == "dataset_adapter":
        sr_assumed = True

    for model_id, model in registry.items():
        status = model.get("status", "pending")
        if status in ("research_only",):
            continue
        reasons: list[str] = []
        missing: list[str] = []
        inp = model.get("input", {}) or {}
        native_contract = inp.get("unit") == "dataset_native"
        stage = manifest.get("metadata_evidence", {}).get("input_stage", {})
        declared_contract = (stage.get("value") == inp.get("input_stage") and
                             stage.get("source") in ("file_header", "user_confirmed"))
        modalities = model.get("modalities", [])

        if modality == "eeg" and "eeg" not in modalities:
            continue
        if modality == "video" and "video" not in modalities and "face_video" not in modalities:
            continue

        verdict = "compatible"
        if status in ("pending", "planned"):
            reasons.append(f"权重状态 {status}，尚未登记文件与 sha256")
            verdict = "incompatible"
        if status == "registered" and (not model.get("file_uri") or not model.get("sha256")):
            reasons.append("登记信息不完整：缺少权重文件地址或 sha256")
            verdict = "incompatible"

        # 模型声明缺失通道可 mask：registry 用 missing_channel_policy: mask（旧名 supports_channel_mask 也认）
        mask_ok = bool(inp.get("supports_channel_mask")) or inp.get("missing_channel_policy") == "mask"

        if modality == "eeg":
            if native_contract:
                if not declared_contract or unit.get("value") != "dataset_native":
                    reasons.append(f"需要明确确认 {inp.get('input_stage')} 波形处理契约并保留 dataset_native 数值尺度；文件形状不能代替确认")
                    verdict = "incompatible"
                from eegagent.worker.readers import MNE_EXTS
                if '.' + manifest.get("source_format", "") in MNE_EXTS:
                    reasons.append("此读取器已转换为微伏，当前权重的原数字尺度与物理电压换算尚未核实")
                    verdict = "incompatible"
                if manifest.get("reference") != inp.get("reference"):
                    reasons.append(f"需要模型训练时的 {inp.get('reference')} 参考契约")
                    verdict = "incompatible"
                mode = manifest.get("metadata_evidence", {}).get("task_mode", {}).get("value", "unknown")
                if inp.get("task_mode") and mode not in ("unknown", inp["task_mode"]):
                    reasons.append(f"模型仅验证 {inp['task_mode']} 模式，当前记录为 {mode}")
                    verdict = "incompatible"
                declaration = inp.get("preprocessing_required")
                if declaration and manifest.get("metadata_evidence", {}).get("preprocessing", {}).get("value") != declaration:
                    reasons.append(f"需要确认输入已符合 {declaration} 的波形准备约定")
                    verdict = "incompatible"
                baseline = manifest.get("metadata_evidence", {}).get("baseline_duration_s", {})
                if ("baseline_s" in inp and baseline.get("source") in ("file_header", "user_confirmed") and
                        baseline.get("value") != inp["baseline_s"]):
                    reasons.append(f"已声明基线 {baseline.get('value')} 秒，与模型所需 {inp['baseline_s']} 秒冲突")
                    verdict = "incompatible"
            expected = _channel_order_names(model)
            n_expected = inp.get("channels")
            if expected:
                have = set(chan_names)
                missing = [c for c in expected if normalize_channel(c) not in have]
                duplicates = [c for c in expected if chan_counts[normalize_channel(c)] > 1]
                if duplicates:
                    reasons.append(f"必需电极有重复或归一化后同名通道：{', '.join(duplicates)}；需明确每道的真实电极")
                    verdict = "incompatible"
                if not layout_known:
                    reasons.append("通道位置尚未确认，不能仅按名称或格式假设映射模型顺序")
                    verdict = "incompatible"
                elif missing and len(missing) == len(expected):
                    reasons.append("没有任何通道与模型顺序匹配")
                    verdict = "incompatible"
                elif missing:
                    if mask_ok:
                        reasons.append(f"缺 {len(missing)} 个通道，模型已声明支持通道 mask")
                        if verdict == "compatible":
                            verdict = "mappable"
                    else:
                        reasons.append(f"缺 {len(missing)} 个通道（{', '.join(missing)}）；当前模型未声明缺失通道处理方法")
                        verdict = "incompatible"
            elif n_expected and manifest.get("n_channels") != n_expected:
                if mask_ok and layout_known:
                    reasons.append(f"文件 {manifest.get('n_channels')} 通道，模型模板 {n_expected} 通道；缺失通道按 mask 处理")
                    if verdict == "compatible":
                        verdict = "mappable"
                elif mask_ok:
                    reasons.append("通道名未知，无法映射到模型通道模板")
                    verdict = "incompatible"
                else:
                    reasons.append(f"模型需要 {n_expected} 通道，文件有 {manifest.get('n_channels')}")
                    verdict = "incompatible"
            elif inp.get("channel_names_required") and not layout_known:
                reasons.append("模型需要 10-20 通道名，文件通道名未知")
                verdict = "incompatible"

            want_sr = inp.get("sampling_rate_hz")
            if not sr:
                reasons.append("采样率未知，无法确认模型输入时间尺度")
                verdict = "incompatible"
            elif sr_assumed:
                reasons.append("采样率只是格式假设或来源未记录，需确认后准备模型输入")
                verdict = "incompatible"
            if want_sr and sr and abs(float(sr) - float(want_sr)) > 1e-6:
                reasons.append(f"需重采样 {sr} → {want_sr} Hz")
                if native_contract:
                    reasons.append(f"输入须保持训练采样率 {want_sr} Hz，由专属模型配方处理")
                    verdict = "incompatible"
                elif verdict == "compatible":
                    verdict = "mappable"
            if not unit_known and not native_contract:
                reasons.append("物理单位尚未确认，模型幅值输入需核对")
                if verdict == "compatible":
                    verdict = "mappable"
            if inp.get("sync_required") and "face_video" in modalities:
                reasons.append("需要同步的面部视频")
            win = inp.get("trial_s") or inp.get("window_s")
            dur = manifest.get("duration_s")
            if win and dur is not None and dur < win:
                reasons.append(f"时长 {dur} s 短于模型所需完整分析块 {win} s")
                verdict = "incompatible"

        results.append({"model_id": model_id, "status": verdict, "reasons": reasons, "missing_channels": missing})
    return results
