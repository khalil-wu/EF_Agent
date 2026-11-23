import hashlib
import json

from eegagent.knowledge.__main__ import main
from eegagent.knowledge.audit import audit_sources
from eegagent.knowledge.chunking import chunk_markdown


def test_source_audit_distinguishes_text_location_from_missing_or_secondary_evidence(tmp_path):
    (tmp_path / "papers").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "papers/paper_x.md").write_text(
        "---\ndoc_id: paper_x\ntitle: Source\ndoc_type: paper\n---\n\n"
        "## Methods\nOriginal measured response.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes/secondary.md").write_text(
        "---\ndoc_id: secondary\ntitle: Summary\n---\n\n## Methods\nA secondary summary.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes/note.md").write_text(
        "---\ndoc_id: note\ntitle: Note\n---\n\n## Claim\n"
        "[located](../papers/paper_x.md#Methods:~:text=measured%20response)\n"
        "[wrong quote](../papers/paper_x.md#Methods:~:text=invented%20result)\n"
        "[wrong section](../papers/paper_x.md#Nonexistent)\n"
        "[absent](../papers/absent.md#Methods)\n"
        "[secondary](../papers/secondary.md#Methods)\n",
        encoding="utf-8",
    )
    result = audit_sources(tmp_path)
    assert result["n_bindings"] == 5 and result["n_located"] == 1 and result["n_unresolved"] == 4
    assert {row["status"] for row in result["bindings"]} == {
        "located", "missing_quote", "missing_section", "missing_document", "not_primary_source",
    }
    located = next(row for row in result["bindings"] if row["status"] == "located")
    assert located["quote_anchored"] and len(located["source_chunk_ids"]) == 1


def test_source_audit_cli_is_offline_and_strict_reports_broken_links(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EEGAGENT_KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setenv("EEGAGENT_EMBED_BASE_URL", "https://must-not-be-called.invalid/v1")
    (tmp_path / "note.md").write_text(
        "---\ndoc_id: note\ntitle: N\n---\n\n## Claim\n[Missing](../papers/missing.md#Methods)\n",
        encoding="utf-8",
    )
    assert main(["audit", "--strict"]) == 1
    assert json.loads(capsys.readouterr().out)["n_unresolved"] == 1
    assert main(["audit"]) == 0
    assert json.loads(capsys.readouterr().out)["n_located"] == 0


def test_original_download_requires_present_hash_matching_source_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    document = tmp_path / "paper.md"
    original = tmp_path / "source.txt"
    content = b"Original source bytes.\n"
    sha256 = hashlib.sha256(content).hexdigest()
    document.write_text(
        "---\ndoc_id: paper_x\ntitle: Source\ndoc_type: paper\n---\n\n## Methods\nOriginal excerpt.\n",
        encoding="utf-8",
    )
    document.with_suffix(".source.json").write_text(json.dumps({
        "path": str(original), "sha256": sha256, "format": "txt", "name": "source.txt",
        "extraction_method": "reviewed_source_line_excerpt", "image_content_extracted": False,
        "untranscribed_formula_pages": [], "verified_formula_pages": [],
    }), encoding="utf-8")
    chunk = chunk_markdown(document)[0]
    for missing_or_wrong in (None, b"Wrong bytes.\n"):
        if missing_or_wrong is not None:
            original.write_bytes(missing_or_wrong)
        row = chunk.row()
        assert row["source_original_available"] is False and "source_original_url" not in row
        assert row["original_url"] and row["parse_note"]
    original.write_bytes(content)
    row = chunk.row()
    assert row["source_original_available"] is True
    assert sha256 in row["source_original_url"]
    cached = tmp_path / "storage/knowledge_sources" / f"{sha256}.txt"
    assert cached.read_bytes() == content
    cached.write_bytes(b"Tampered cache.\n")
    row = chunk.row()
    assert row["source_original_available"] is False and "source_original_url" not in row
    assert original.read_bytes() == content
