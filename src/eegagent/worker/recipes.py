"""Export an executable replay of a recorded native analysis chain."""
from pprint import pformat

from eegagent.worker.processing import _public
from eegagent.worker.store import artifact_payload_path, load_artifact, load_manifest, make_artifact_id, save_artifact


def export_python(source_id: str) -> dict:
    from eegagent.worker.tools import TOOL_DEFINITIONS
    from eegagent.worker.outputs import VERSION

    versions = {tool["name"]: tool["version"] for tool in TOOL_DEFINITIONS}
    required, inputs, body, variables = {}, [], [], {}
    snapshots, lineage = [], []

    def require(tool, version=None):
        actual = versions.get(tool)
        if actual is None or (version is not None and version != actual):
            raise ValueError(f"无法导出复现脚本：{tool}@{version} 与当前可用版本 {actual} 不一致")
        required[tool] = actual

    def emit(identifier):
        if identifier in variables:
            return variables[identifier]
        if not identifier.startswith("art_"):
            manifest = load_manifest(identifier)
            if manifest["modality"] != "eeg":
                raise ValueError("复现脚本目前支持 EEG 原生分析链")
            evidence = manifest.get("metadata_evidence", {})
            inspect_args = {key: manifest[key] for key in ("sample_rate_hz", "channel_names")
                            if evidence.get(key, {}).get("source") == "user_confirmed"}
            if manifest.get("trial_index") is not None:
                inspect_args["trial_index"] = manifest["trial_index"]
            metadata = {key: value["value"] for key, value in evidence.items()
                        if key in {"unit", "reference", "baseline_duration_s", "label_names", "task_mode", "preprocessing"}
                        and value.get("source") == "user_confirmed"}
            if manifest.get("unit", {}).get("source") == "user_confirmed":
                metadata["unit"] = manifest["unit"]["value"].replace("µV", "uV")
            if evidence.get("dataset", {}).get("source") == "user_declared":
                metadata["dataset"] = manifest["dataset"]
            index = len(inputs)
            inputs.append({"path": manifest["source_path"], "inspect": inspect_args, "metadata": metadata})
            snapshots.append({key: manifest.get(key) for key in ("source_path", "attachment_sha256", "trial_index", "sample_rate_hz", "channel_names", "unit")})
            require("inspect_record")
            variable = f"source_{index + 1}"
            body.append(f'{variable} = eeg.call("inspect_record", path=INPUTS[{index}]["path"], **INPUTS[{index}]["inspect"])["manifest"]["manifest_id"]')
            if metadata:
                require("update_record_metadata")
                body.append(f'{variable} = eeg.call("update_record_metadata", manifest_id={variable}, **INPUTS[{index}]["metadata"])["manifest"]["manifest_id"]')
            variables[identifier] = variable
            return variable

        record = load_artifact(identifier)
        provenance = record["provenance"]
        tool, _, version = provenance["created_by"].partition("@")
        params = dict(provenance["params"])
        require(tool, version)
        if tool not in {"preprocess", "clean_artifacts", "segment_signal", "extract_features"}:
            raise ValueError(f"无法导出复现脚本：步骤 {provenance['created_by']} ({record['kind']}) 尚不支持原生重放")
        dependency = params.pop("windows" if tool == "extract_features" else "source")
        parent = emit(dependency)
        if tool == "segment_signal":
            span = record.get("analysis", {}).get("source_time_range_s")
            if span is None:
                raise ValueError("无法导出复现脚本：segment_signal 记录缺少精确 source_time_range_s")
            params.update(start_s=span[0], end_s=span[1])
        if tool == "clean_artifacts":
            params["plot"] = False
        variable = f"step_{len(lineage) + 1}"
        argument = "windows_id" if tool == "extract_features" else "source_id"
        body.append(f'{variable} = eeg.call({tool!r}, {argument}={parent}, **{params!r})["artifact"]["artifact_id"]')
        lineage.append(provenance["created_by"])
        variables[identifier] = variable
        return variable

    target = source_id
    output_kind = None
    if target.startswith("art_"):
        record = load_artifact(target)
        if record["provenance"]["created_by"].startswith("export@"):
            params = record["provenance"]["params"]
            output_kind, target = params["kind"], params["source"]
            if output_kind not in {"csv", "json", "signal_csv", "signal_edf"}:
                raise ValueError(f"无法导出复现脚本：不支持 export(kind={output_kind!r}) 作为数值重放目标")
        target_record = load_artifact(target) if target.startswith("art_") else load_manifest(target)
        kind = target_record.get("kind", "normalized_signal")
        if kind not in {"normalized_signal", "windows", "features"}:
            created_by = target_record.get("provenance", {}).get("created_by", kind)
            raise ValueError(f"无法导出复现脚本：不支持 {created_by} ({kind})")
        output_kind = output_kind or ("signal_csv" if kind == "normalized_signal" else "csv")
    else:
        output_kind = "signal_csv"
    result_variable = emit(target)
    require("export")
    body.extend([f"RESULT_ID = {result_variable}",
                 f'output = eeg.call("export", kind={output_kind!r}, source_id=RESULT_ID)',
                 "if OUTPUT_PATH:",
                 '    eeg.call("save_to_workspace", artifact_id=output["artifact"]["artifact_id"], path=OUTPUT_PATH)',
                 'print("Replayed result:", RESULT_ID, "export:", output["artifact"]["artifact_id"])'])
    script = "\n".join([
        "# Run this pure Python file with EFAgent run_python (the eeg API is preloaded).",
        "# INPUTS capture the effective recorded input metadata, not an invented historical call log.",
        "# Change paths/parameters here to apply this recorded native recipe to another input.",
        "from eegagent.worker.tools import TOOL_DEFINITIONS", "",
        f"INPUTS = {pformat(inputs, sort_dicts=False, width=100)}",
        'OUTPUT_PATH = None  # Optional workspace path, e.g. "results/replayed.csv"; existing files are not overwritten.',
        f"REQUIRED_VERSIONS = {required!r}",
        'available_versions = {tool["name"]: tool["version"] for tool in TOOL_DEFINITIONS}',
        "for name, expected in REQUIRED_VERSIONS.items():",
        "    if available_versions.get(name) != expected:",
        '        raise RuntimeError(f"{name}: recipe needs {expected}, installed {available_versions.get(name)}")',
        "", *body, "",
    ])
    identifier = make_artifact_id("export", VERSION, {"kind": "python", "source": source_id})
    path = artifact_payload_path(identifier, "py")
    path.write_text(script, encoding="utf-8")
    source_record = load_artifact(source_id) if source_id.startswith("art_") else load_manifest(source_id)
    record = save_artifact({"artifact_id": identifier, "kind": "report", "media_type": "text/x-python", "uri": str(path),
                            "source_manifest_ids": source_record.get("source_manifest_ids", [source_id]),
                            "parent_artifact_ids": [source_id] if source_id.startswith("art_") else [],
                            "summary": {"title": "analysis-recipe.py", "format": "py", "step_count": len(lineage),
                                        "input_count": len(inputs), "tool_versions": required, "native_steps": lineage,
                                        "input_strategy": "effective_manifest_snapshot", "inputs": snapshots},
                            "provenance": {"created_by": f"export@{VERSION}", "params": {"kind": "python", "source": source_id},
                                           "steps": ["export_recorded_native_recipe"]}})
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"]}
