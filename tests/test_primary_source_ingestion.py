import hashlib
import json

import pymupdf

from eegagent.knowledge.chunking import chunk_markdown
from eegagent.knowledge.ingest import ingest_file


def test_pdf_ingestion_keeps_physical_pages_references_and_original(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    pdf = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Methods\nThe recording uses a 500 Hz sample rate.")
        document.new_page().insert_text((72, 72), "References\n[1] Source used by the experiment.\nLimits\nNo individual diagnosis is supported.")
        document.save(pdf)
    original = pdf.read_bytes()
    out = ingest_file(pdf, doc_id="paper_test", title="Test paper", out_dir=tmp_path / "papers", url="https://example.org/paper")
    rows = [chunk.row() for chunk in chunk_markdown(out)]
    assert {row["page"] for row in rows} == {1, 2}
    assert any("[1] Source" in row["text"] for row in rows if row["page"] == 2)
    assert any("No individual diagnosis" in row["text"] for row in rows)
    assert all(row["source_kind"] == "paper_excerpt" for row in rows)
    digest = hashlib.sha256(original).hexdigest()
    assert (tmp_path / "storage/knowledge_sources" / f"{digest}.pdf").read_bytes() == original
    assert rows[0]["source_original_url"].endswith(f"{digest}/file?format=pdf")


def test_official_html_ingestion_selects_body_and_preserves_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    html = tmp_path / "docs.html"
    html.write_text('<html><nav>Unrelated menu</nav><main><h1>EEG reference</h1><p>Voltage units are volts.</p><table><tr><th>Channel</th><th>Unit</th></tr><tr><td>F3</td><td>V</td></tr></table></main></html>', encoding="utf-8")
    out = ingest_file(html, doc_id="doc_reference", title="Official reference", doc_type="documentation", out_dir=tmp_path / "references")
    row = chunk_markdown(out)[0].row()
    assert row["source_kind"] == "official_documentation"
    assert "Unrelated menu" not in row["text"]
    assert "F3" in row["text"] and "|" in row["text"]
    assert "page" not in row
    assert row["source_original_url"].endswith("?format=html")


def test_same_parsed_text_keeps_distinct_original_source_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    html = tmp_path / "docs.html"
    html.write_text('<main><h1>Methods</h1><p>A fixed reference is required.</p></main>', encoding="utf-8")
    first_original = html.read_bytes()
    out = ingest_file(html, doc_id="doc_version", title="Versioned reference", doc_type="documentation", out_dir=tmp_path / "references")
    first = chunk_markdown(out)[0].row()
    html.write_text(html.read_text(encoding="utf-8") + "<!-- updated raw source -->", encoding="utf-8")
    ingest_file(html, doc_id="doc_version", title="Versioned reference", doc_type="documentation", out_dir=tmp_path / "references")
    second = chunk_markdown(out)[0].row()
    assert first["text"] == second["text"]
    assert first["chunk_id"] != second["chunk_id"]
    assert first["source_original_url"] != second["source_original_url"]
    archived = tmp_path / "storage/knowledge_sources" / f"{first['source_document_sha256']}.html"
    assert archived.read_bytes() == first_original


def test_repeated_source_sections_have_distinct_citable_positions(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    html = tmp_path / "repeated.html"
    section = "<h2>Limit</h2><p>The equation is available in the original source.</p>"
    html.write_text("<main>" + section + section + "</main>", encoding="utf-8")
    out = ingest_file(html, doc_id="doc_repeated", title="Repeated sections", doc_type="documentation", out_dir=tmp_path / "references")
    rows = [chunk.row() for chunk in chunk_markdown(out)]
    assert len(rows) == 2 and rows[0]["text"] == rows[1]["text"]
    assert rows[0]["chunk_id"] != rows[1]["chunk_id"]
    assert rows[0]["parsed_start_offset"] < rows[1]["parsed_start_offset"]
    snapshot = out.read_text(encoding="utf-8")
    assert all(snapshot[row["parsed_start_offset"]:row["parsed_end_offset"]] == row["text"] for row in rows)
