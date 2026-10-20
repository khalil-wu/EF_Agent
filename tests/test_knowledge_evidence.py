import importlib
import json
from pathlib import Path

import pytest

from eegagent.knowledge import KnowledgeIndex
from eegagent.knowledge.documents import parse_document
from eegagent.knowledge.index import search_knowledge
from eegagent.worker.knowledge import knowledge_graph


@pytest.fixture
def evidence_index(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "knowledge"
    root.mkdir()
    docs = {
        "match": ("datasets", "SensorNet SensorNet SensorNet recording methods."),
        "support_a": ("methods", "The recording uses 250 Hz sampling and eight channels."),
        "support_b": ("methods", "Welch estimates the power spectral density from overlapping windows."),
        "support_c": ("methods", "A common average reference subtracts the channel mean."),
    }
    for doc_id, (domain, body) in docs.items():
        (root / f"{doc_id}.md").write_text(
            f"---\ndoc_id: {doc_id}\ntitle: {doc_id}\ndomain: {domain}\nsource: source-{doc_id}\n---\n\n## Methods\n\n{body}\n",
            encoding="utf-8",
        )
    graph = root / "graph"
    graph.mkdir()
    (graph / "seed.yaml").write_text(
        "entities:\n"
        "  - {id: dataset_sensor, type: dataset, name: SensorNet, refs: [match]}\n"
        "  - {id: device_x, type: device, name: Sensor, refs: [support_a]}\n"
        "  - {id: method_welch, type: method, name: Welch, refs: [support_b]}\n"
        "  - {id: method_car, type: method, name: CAR, refs: [support_c]}\n"
        "relations:\n"
        "  - {id: r_a, src: dataset_sensor, rel: recorded_with, dst: device_x, evidence: [support_a], confidence: 1}\n"
        "  - {id: r_b, src: dataset_sensor, rel: applies_to, dst: method_welch, evidence: [support_b], confidence: 0.9}\n"
        "  - {id: r_c, src: dataset_sensor, rel: applies_to, dst: method_car, evidence: [support_c], confidence: 0.8}\n"
        "  - {id: r_shared, src: dataset_sensor, rel: reports, dst: device_x, evidence: [support_a], confidence: 0.7}\n"
        "  - {id: r_missing, src: dataset_sensor, rel: requires_input, dst: device_x, evidence: [missing]}\n",
        encoding="utf-8",
    )
    idx = KnowledgeIndex(root, embedder=None, reranker=None)
    monkeypatch.setattr(importlib.import_module("eegagent.knowledge.index"), "_INDEX", idx)
    return idx


def _assert_readable_evidence(result):
    chunks = {row["chunk_id"]: row for row in result["chunks"]}
    assert len(chunks) == len(result["chunks"])
    for fact in result.get("facts", result.get("path", [])):
        assert fact["evidence"]
        assert all(chunks[cid]["text"].strip() for cid in fact["evidence"])


def test_graph_expansion_keeps_matches_and_caps_supports(evidence_index):
    result = evidence_index.search("SensorNet", k=1)
    matches = [row for row in result["chunks"] if row["retrieval_role"] == "match"]
    supports = [row for row in result["chunks"] if row["retrieval_role"] == "graph_support"]
    assert [row["doc_id"] for row in matches] == ["match"]
    assert {row["doc_id"] for row in supports} == {"support_a", "support_b"}
    assert {fact["fact_id"] for fact in result["facts"]} == {"r_a", "r_b", "r_shared"}
    _assert_readable_evidence(result)
    for row in supports:
        original = evidence_index.get(row["chunk_id"])
        assert row["text"] == original["text"]
        assert row["original_url"] == original["original_url"]
        assert row["document_sha256"] == original["document_sha256"]


def test_uploaded_rank_fusion_does_not_drop_graph_supports(evidence_index, tmp_path):
    upload = tmp_path / "recording-notes.md"
    upload.write_text("# Acquisition\nSensorNet participant notes and recording protocol.", encoding="utf-8")
    parse_document(upload, "doc_notes", "ses_notes")
    sessions = tmp_path / "storage/sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / "ses_notes.json").write_text(json.dumps({"manifests": [{"manifest_id": "doc_notes", "modality": "document"}]}), encoding="utf-8")
    result = search_knowledge("SensorNet", k=1, session_id="ses_notes")
    matches = [row for row in result["chunks"] if row["retrieval_role"] == "match"]
    assert len(matches) == 1 and matches[0]["doc_id"] == "doc_notes"
    assert matches[0]["page"] == 1 and matches[0]["source_kind"] == "user_upload"
    assert {fact["fact_id"] for fact in result["facts"]} == {"r_a", "r_b", "r_shared"}
    _assert_readable_evidence(result)


def test_domain_filter_does_not_leak_out_of_scope_graph_facts(evidence_index):
    result = search_knowledge("SensorNet", k=1, domain="datasets", scope="project")
    assert result["chunks"] and all(row["domain"] == "datasets" for row in result["chunks"])
    assert result["facts"] == []


def test_graph_neighborhood_and_path_include_their_sources(evidence_index):
    result = knowledge_graph(entity="SensorNet")
    _assert_readable_evidence(result)
    assert {fact["fact_id"] for fact in result["facts"]} == {"r_a", "r_b", "r_shared"}
    path = knowledge_graph(path_from="SensorNet", path_to="Welch")
    assert [fact["fact_id"] for fact in path["path"]] == ["r_b"]
    _assert_readable_evidence(path)


def test_graph_path_with_unresolved_relation_is_not_reported(evidence_index):
    relation = evidence_index.graph.relations["r_b"]
    relation.chunk_ids = []
    relation.unresolved = True
    result = knowledge_graph(path_from="SensorNet", path_to="Welch")
    assert result["path"] == [] and result["chunks"] == []
    assert "缺少可读取" in result["note"]


def test_extracted_quote_resolves_to_the_chunk_that_contains_it(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "knowledge"
    extracted = root / "graph" / "extracted"
    extracted.mkdir(parents=True)
    quote = "An independently located source sentence supports the relationship."
    body = "ExampleEEG introduces a signal processing method.\n\n" + ("Background methods are described in this passage. " * 36) + "\n\n" + quote
    (root / "paper.md").write_text(f"---\ndoc_id: long_paper\n---\n\n## Methods\n\n{body}\n", encoding="utf-8")
    entries = [
        {"kind": "entity", "id": "dataset_example", "type": "dataset", "name": "ExampleEEG", "refs": ["long_paper"], "status": "accepted"},
        {"kind": "entity", "id": "method_example", "type": "method", "name": "ExampleMethod", "refs": ["long_paper"], "status": "accepted"},
        {"kind": "relation", "id": "r_located", "src": "dataset_example", "rel": "applies_to", "dst": "method_example",
         "evidence": ["long_paper#Methods"], "evidence_quote": quote, "quote_located": True, "status": "accepted"},
        {"kind": "relation", "id": "r_stale", "src": "dataset_example", "rel": "reports", "dst": "method_example",
         "evidence": ["long_paper#Methods"], "evidence_quote": "This sentence is absent from the source.", "quote_located": True, "status": "accepted"},
    ]
    (extracted / "paper.jsonl").write_text("\n".join(json.dumps(row) for row in entries), encoding="utf-8")
    idx = KnowledgeIndex(root, embedder=None, reranker=None)
    first = idx.chunks[0].chunk_id
    relation = idx.graph.relations["r_located"]
    assert first not in relation.chunk_ids
    assert all(quote in idx.by_id[cid].text for cid in relation.chunk_ids)
    result = idx.search("ExampleEEG", k=1)
    assert [fact["fact_id"] for fact in result["facts"]] == ["r_located"]
    assert result["facts"][0]["evidence_quote"] == quote
    assert idx.graph.relations["r_stale"].unresolved
    _assert_readable_evidence(result)
