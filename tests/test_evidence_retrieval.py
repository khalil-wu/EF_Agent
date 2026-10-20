import hashlib
import json

from eegagent.knowledge import KnowledgeIndex
from eegagent.knowledge.chunking import chunk_markdown
from eegagent.knowledge.ingest import extract_graph, review


def test_references_expose_their_role_and_content_without_verifying_record_origin(tmp_path, monkeypatch):
    root = tmp_path / "knowledge"
    root.mkdir()
    storage = tmp_path / "storage"
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(storage))
    note = root / "deap.md"
    original = "---\ndoc_id: d\ntitle: DEAP\nsource: project summary\n---\n\n## Format\nA matching shape does not prove origin.\n"
    note.write_text(original, encoding="utf-8")
    index = KnowledgeIndex(root, embedder=None, reranker=None)
    row = index.search("DEAP shape")["chunks"][0]
    assert row["source_kind"] == "project_note" and row["evidence_role"] == "reference"
    old_snapshot = storage / "knowledge_sources" / f"{row['document_sha256']}.md"
    assert old_snapshot.read_bytes() == original.encode("utf-8")
    assert hashlib.sha256(old_snapshot.read_bytes()).hexdigest() == row["document_sha256"]
    assert row["text"] in old_snapshot.read_text(encoding="utf-8")
    assert row["original_url"] == f"/knowledge/documents/{row['document_sha256']}/file"

    updated = original.replace("matching shape", "file name")
    note.write_text(updated, encoding="utf-8")
    current = KnowledgeIndex(root, embedder=None, reranker=None)
    after = current.search("DEAP file name")["chunks"][0]
    assert current.get(row["chunk_id"]) is None
    assert row["chunk_id"] != after["chunk_id"] and row["content_sha256"] != after["content_sha256"]
    assert old_snapshot.read_bytes() == original.encode("utf-8")
    new_snapshot = storage / "knowledge_sources" / f"{after['document_sha256']}.md"
    assert new_snapshot.read_bytes() == updated.encode("utf-8")
    repeat = KnowledgeIndex(root, embedder=None, reranker=None).get(after["chunk_id"])
    assert after["retrieval_role"] == "match" and "retrieval_role" not in repeat
    assert repeat == {key: value for key, value in after.items() if key not in ("score", "retrieval_role")}


def test_chunk_identity_keeps_unchanged_excerpt_bound_to_its_document_version(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    note = tmp_path / "note.md"
    original = "---\ndoc_id: d\ntitle: D\n---\n\n## Methods\nWelch estimates PSD.\n\n## Limits\nNo classifier ran.\n"
    note.write_text(original, encoding="utf-8")
    before = chunk_markdown(note)[0].row()
    note.write_text(original.replace("No classifier ran.", "The file origin is unknown."), encoding="utf-8")
    after = chunk_markdown(note)[0].row()
    assert before["text"] == after["text"] and before["content_sha256"] == after["content_sha256"]
    assert before["chunk_id"] != after["chunk_id"]
    assert before["document_sha256"] != after["document_sha256"]
    assert before["original_url"] != after["original_url"]


def test_long_project_excerpts_preserve_original_paragraph_separators(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    note = tmp_path / "long.md"
    original = "---\ndoc_id: d\ntitle: D\n---\n\n## Results\n" + "\n \t\n\n\n".join(
        f"Paragraph {number}: " + "measured alpha power " * 27 for number in range(8))
    note.write_text(original, encoding="utf-8")
    rows = [chunk.row() for chunk in chunk_markdown(note)]
    assert len(rows) > 1
    snapshot = tmp_path / "storage/knowledge_sources" / f"{rows[0]['document_sha256']}.md"
    assert snapshot.read_bytes() == original.encode("utf-8")
    assert all(row["text"] in original for row in rows)
    indexed_body = "## Results\n" + original.split("## Results\n", 1)[1]
    assert "".join("".join(row["text"] for row in rows).split()) == "".join(indexed_body.split())


def test_forged_quote_tail_is_not_promoted_to_document_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_KNOWLEDGE_DIR", str(tmp_path))
    prefix = "a supported sentence with original wording " * 3
    original = prefix + "the study observes an association."
    (tmp_path / "paper.md").write_text("---\ndoc_id: paper_x\ntitle: X\ndoc_type: paper\n---\n\n## Results\n" + original, encoding="utf-8")
    index = KnowledgeIndex(tmp_path, embedder=None, reranker=None)

    class FakeLLM:
        def chat(self, messages):
            return json.dumps({"entities": [
                {"id": "method_x", "type": "method", "name": "X"},
                {"id": "construct_y", "type": "construct", "name": "Y"}],
                "relations": [
                    {"src": "method_x", "rel": "indicates", "dst": "construct_y", "evidence_quote": prefix + "official provenance is proven."},
                    {"src": "paper_x", "rel": "reports", "dst": "construct_y", "evidence_quote": original}]})

    rows = extract_graph("paper_x", FakeLLM(), index)
    forged, supported = [row for row in rows if row["kind"] == "relation"]
    assert forged["evidence"] == [] and not forged["quote_located"]
    assert supported["evidence"] == ["paper_x#Results"] and supported["quote_located"]
    result = review("paper_x", accept_all=True)
    assert result["unsupported"] == 1
    stored = [json.loads(line) for line in (tmp_path / "graph/extracted/paper_x.jsonl").read_text(encoding="utf-8").splitlines()]
    assert stored[-2]["status"] == "proposed" and stored[-1]["status"] == "accepted"


def test_legacy_unlocated_quote_cannot_gain_evidence_from_first_document_chunk(tmp_path):
    (tmp_path / "paper.md").write_text("---\ndoc_id: d\ntitle: D\n---\n\n## Text\nOriginal evidence.\n", encoding="utf-8")
    directory = tmp_path / "graph/extracted"
    directory.mkdir(parents=True)
    rows = [
        {"kind": "entity", "id": "method_a", "type": "method", "name": "A", "refs": ["d"], "status": "accepted"},
        {"kind": "entity", "id": "method_b", "type": "method", "name": "B", "refs": ["d"], "status": "accepted"},
        {"kind": "relation", "src": "method_a", "rel": "indicates", "dst": "method_b", "evidence": ["d"], "quote_located": False, "status": "accepted"},
        {"kind": "relation", "src": "method_a", "rel": "derived_from", "dst": "method_b", "evidence": ["d"], "quote_located": False, "status": "rejected"},
    ]
    (directory / "d.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    index = KnowledgeIndex(tmp_path, embedder=None, reranker=None)
    assert not index.graph.relations
    index = KnowledgeIndex(tmp_path, embedder=None, reranker=None, include_proposed=True)
    (relation,) = index.graph.relations.values()
    assert relation.status == "proposed" and relation.unresolved and relation.chunk_ids == []
