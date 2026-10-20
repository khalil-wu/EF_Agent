import importlib
import json
from pathlib import Path

import yaml

from eegagent.knowledge import KnowledgeIndex
from eegagent.knowledge.documents import parse_document
from eegagent.knowledge.evaluate import evaluate
from eegagent.knowledge.index import search_knowledge


def source_index(tmp_path, monkeypatch, section='Methods'):
    root = tmp_path / 'knowledge'
    (root / 'methods').mkdir(parents=True)
    (root / 'papers').mkdir()
    monkeypatch.setenv('EEGAGENT_STORAGE_DIR', str(tmp_path / 'storage'))
    (root / 'methods/note.md').write_text(
        '---\ndoc_id: note\ntitle: SensorNet\ndomain: methods\n---\n\n## Definition\n'
        'SensorNet SensorNet SensorNet measures differential entropy.\n'
        f'[Original method](../papers/original.md#{section})\n', encoding='utf-8')
    (root / 'papers/original.md').write_text(
        '---\ndoc_id: original\ntitle: Original study\ndoc_type: paper\ndomain: methods\n---\n\n'
        '## Methods\nThe experiment used six participants and binary labels.\n\n'
        '## Results\nPerformance was evaluated within each participant.\n', encoding='utf-8')
    idx = KnowledgeIndex(root, embedder=None, reranker=None)
    monkeypatch.setattr(importlib.import_module('eegagent.knowledge.index'), '_INDEX', idx)
    return idx


def test_original_is_delivered_separately_with_exact_excerpt_and_note_link(tmp_path, monkeypatch):
    idx = source_index(tmp_path, monkeypatch)
    result = search_knowledge('SensorNet', k=1, scope='project')
    original, note = result['chunks']
    assert note['doc_id'] == 'note' and note['retrieval_role'] == 'match'
    assert original['doc_id'] == 'original' and original['section'] == 'Methods'
    assert original['source_kind'] == 'paper_excerpt' and original['retrieval_role'] == 'source_support'
    assert original['supports_notes'] == [note['chunk_id']]
    saved = idx.get(original['chunk_id'])
    assert original['text'] == saved['text']
    assert original['original_url'] == saved['original_url']
    assert 'six participants and binary labels' in original['text']


def test_upload_rank_fusion_does_not_attach_an_unselected_notes_original(tmp_path, monkeypatch):
    source_index(tmp_path, monkeypatch)
    upload = tmp_path / 'user.md'
    upload.write_text('# Data\nSensorNet participant notes.', encoding='utf-8')
    parse_document(upload, 'doc_user', 'ses_user')
    sessions = tmp_path / 'storage/sessions'
    sessions.mkdir()
    (sessions / 'ses_user.json').write_text(json.dumps({'manifests': [{'manifest_id': 'doc_user', 'modality': 'document'}]}), encoding='utf-8')
    result = search_knowledge('SensorNet', k=1, session_id='ses_user')
    assert [row['doc_id'] for row in result['chunks']] == ['doc_user']


def test_note_filter_and_missing_section_do_not_imply_an_original_was_read(tmp_path, monkeypatch):
    idx = source_index(tmp_path, monkeypatch, section='Missing-section')
    result = idx.search('SensorNet', k=1)
    assert [row['doc_id'] for row in result['chunks']] == ['note']
    qa = tmp_path / 'qa.yaml'
    qa.write_text(yaml.safe_dump([{'q': 'SensorNet', 'docs': ['note'], 'evidence': [
        {'doc_id': 'original', 'section': 'Methods', 'contains': ['six participants']}]}]), encoding='utf-8')
    scores = evaluate(idx, qa, k=1)
    assert scores['doc_hit@1'] == 1 and scores['evidence_hit_rate'] == 0
    idx = source_index(tmp_path / 'valid', monkeypatch)
    assert all(row['doc_type'] == 'note' for row in idx.search('SensorNet', doc_type='note')['chunks'])


def test_text_locator_selects_actual_sentence_and_never_substitutes_another_excerpt(tmp_path, monkeypatch):
    idx = source_index(tmp_path / 'located', monkeypatch, section='Methods:~:text=six%20participants')
    sources = [row for row in idx.search('SensorNet', k=1)['chunks'] if row['retrieval_role'] == 'source_support']
    assert len(sources) == 1 and 'six participants' in sources[0]['text']
    idx = source_index(tmp_path / 'absent', monkeypatch, section='Methods:~:text=an%20absent%20sentence')
    assert [row['doc_id'] for row in idx.search('SensorNet', k=1)['chunks']] == ['note']


def test_reviewed_evidence_is_not_counted_as_a_ranked_document_hit(tmp_path, monkeypatch):
    idx = source_index(tmp_path, monkeypatch)
    qa = tmp_path / 'qa.yaml'
    qa.write_text(yaml.safe_dump([{'q': 'SensorNet', 'docs': ['original'], 'evidence': [
        {'doc_id': 'original', 'section': 'Methods', 'contains': ['six participants', 'binary labels']}]}]), encoding='utf-8')
    scores = evaluate(idx, qa, k=1)
    assert scores['doc_hit@1'] == 0 and scores['evidence_hit_rate'] == 1
    assert scores['evidence_evaluated_n'] == 1


def test_pdf_ligature_locator_matches_without_rewriting_the_excerpt(tmp_path, monkeypatch):
    source_index(tmp_path, monkeypatch, section='Methods:~:text=filter%20settings%20at%20200Hz')
    paper = tmp_path / 'knowledge/papers/original.md'
    paper.write_text(paper.read_text(encoding='utf-8').replace(
        'The experiment used six participants and binary labels.',
        'The experiment used ﬁlter settings at 200Hz.'), encoding='utf-8')
    idx = KnowledgeIndex(tmp_path / 'knowledge', embedder=None, reranker=None)
    sources = [row for row in idx.search('SensorNet', k=1)['chunks'] if row['retrieval_role'] == 'source_support']
    assert len(sources) == 1 and 'ﬁlter settings at 200Hz' in sources[0]['text']
    assert sources[0]['text'] in paper.read_text(encoding='utf-8')


def test_two_claims_deliver_distinct_passages_from_one_original_section(tmp_path, monkeypatch):
    source_index(tmp_path, monkeypatch)
    note = tmp_path / 'knowledge/methods/note.md'
    note.write_text('---\ndoc_id: note\ntitle: SensorNet\ndomain: methods\n---\n\n'
                    '## Definition\nSensorNet filter baseline.\n'
                    '[Filter](../papers/original.md#Methods:~:text=Filter%20is%20four%20Hz)\n'
                    '[Baseline](../papers/original.md#Methods:~:text=Baseline%20is%20three%20seconds)\n', encoding='utf-8')
    paper = tmp_path / 'knowledge/papers/original.md'
    paper.write_text('---\ndoc_id: original\ntitle: Original study\ndoc_type: paper\ndomain: methods\n---\n\n'
                     '## Methods\nFilter is four Hz.\n\n' + 'Measurement details. ' * 100 +
                     '\n\nBaseline is three seconds.\n', encoding='utf-8')
    idx = KnowledgeIndex(tmp_path / 'knowledge', embedder=None, reranker=None)
    sources = [row for row in idx.search('SensorNet filter baseline', k=1)['chunks'] if row['retrieval_role'] == 'source_support']
    assert len(sources) == 2 and len({row['chunk_id'] for row in sources}) == 2
    assert {row['section'] for row in sources} == {'Methods'}
    assert any('Filter is four Hz' in row['text'] for row in sources)
    assert any('Baseline is three seconds' in row['text'] for row in sources)
