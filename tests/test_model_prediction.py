"""Synthetic model responses verify storage/coordinates, not EEG emotion accuracy."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import signals, store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.model_prediction import save_model_prediction
from eegagent.worker.outputs import export, read_artifact
from eegagent.worker.processing import preprocess, segment_signal


@pytest.fixture
def windows(tmp_path, monkeypatch):
    monkeypatch.setenv('EEGAGENT_STORAGE_DIR', str(tmp_path))
    signals._CACHE.clear()
    root = store.ensure_layout()
    t = np.arange(800) / 100
    path = root / 'uploads' / 'signal.npy'
    np.save(path, np.array([20 * np.sin(2 * np.pi * 10 * t), 10 * np.sin(2 * np.pi * 12 * t)]))
    manifest = inspect_record(str(path), sample_rate_hz=100, channel_names=['F4', 'F3'])
    source = preprocess(manifest['manifest_id'], [{'op': 'remove_baseline', 'seconds': 2, 'drop': True}])['artifact']['artifact_id']
    return segment_signal(source, 2)['artifact']['artifact_id']


MODEL = {'model_id': 'fixture_classifier', 'model_version': 'test-only', 'task': 'emotion',
         'input_recipe': {'amplitude_transform': 'none'}, 'weight_sha256': None}
CLASSIFICATION = {'kind': 'classification', 'labels': ['negative', 'positive'], 'score_kind': 'probabilities'}


def test_model_rows_preserve_class_order_original_time_pagination_and_export(windows):
    responses = [[.9, .1], [.2, .8], [.6, .4]]
    result = save_model_prediction(windows, responses, MODEL, CLASSIFICATION, run_id='fixture-run')
    artifact = result['artifact']
    assert artifact['kind'] == 'prediction' and artifact['parent_artifact_ids'] == [windows]
    assert artifact['provenance']['model_id'] == MODEL['model_id']
    assert artifact['provenance']['model_run_id'] == 'fixture-run'
    assert result['summary']['calibration'] == {'status': 'unknown'}
    assert result['summary']['aggregation'] is None
    assert result['summary']['label_counts'] == {'negative': 2, 'positive': 1}
    page = read_artifact(artifact['artifact_id'], part='predictions', start=1, limit=1)
    assert page['total'] == 3 and page['rows'][0]['label'] == 'positive'
    assert page['rows'][0]['scores'] == [.2, .8]
    assert page['rows'][0]['original_time_range_s'] == [4., 6.]
    assert result['summary']['coverage']['original_time_ranges_s'][0]['original_time_range_s'] == [2., 8.]
    exported = export('csv', artifact['artifact_id'])['artifact']
    rows = list(csv.DictReader(Path(exported['uri']).open(encoding='utf-8')))
    assert [row['label'] for row in rows] == ['negative', 'positive', 'negative']
    assert rows[1]['score:positive'] == '0.8' and rows[1]['original_start_s'] == '4.0'
    assert rows[1]['model_version'] == 'test-only' and rows[1]['calibration_status'] == 'unknown'
    full = json.loads(Path(export('json', artifact['artifact_id'])['artifact']['uri']).read_text(encoding='utf-8'))
    assert full['model'] == MODEL and full['predictions'][1] == page['rows'][0]


def test_logits_remain_logits_and_regression_keeps_units(windows):
    logits = [[-2, 4], [3, -8], [-3, -1]]
    record = save_model_prediction(windows, logits, MODEL,
                                   {**CLASSIFICATION, 'score_kind': 'logits'}, run_id='logit-run')
    row = read_artifact(record['artifact']['artifact_id'], part='predictions')['rows'][0]
    assert row['scores'] == [-2., 4.] and row['label'] == 'positive'
    output = {'kind': 'regression', 'dimensions': [{'name': 'valence', 'unit': 'rating_1_9'}, {'name': 'arousal', 'unit': 'rating_1_9'}]}
    regression = save_model_prediction(windows, [[6, 2], [4, 5], [7, 3]], MODEL, output, run_id='regression-run')
    assert regression['summary']['output_kind'] == 'regression' and 'labels' not in regression['summary']
    assert 'label_counts' not in regression['summary']
    row = read_artifact(regression['artifact']['artifact_id'], part='predictions')['rows'][1]
    assert row['values'] == [4., 5.] and 'label' not in row
    exported = export('csv', regression['artifact']['artifact_id'])['artifact']
    rows = list(csv.DictReader(Path(exported['uri']).open(encoding='utf-8')))
    assert rows[1]['value:valence (rating_1_9)'] == '4.0'


def test_incomplete_outputs_and_mislabeled_probabilities_are_not_saved(windows):
    with pytest.raises(ValueError, match='窗口'):
        save_model_prediction(windows, [[.4, .6]], MODEL, CLASSIFICATION, run_id='incomplete')
    with pytest.raises(ValueError, match='概率'):
        save_model_prediction(windows, [[-1, 2]] * 3, MODEL, CLASSIFICATION, run_id='invalid-probabilities')


def test_score_margins_keep_original_times_and_ties_match_the_prediction(windows):
    model = {**MODEL, 'limitations': ['training-scope-detail']}
    result = save_model_prediction(windows, [[2, 2], [4, 3], [-1, 2]], model,
                                   {**CLASSIFICATION, 'score_kind': 'logits'}, run_id='margins')
    margin = result['summary']['decision_margin']
    assert margin['min'] == 0 and margin['median'] == 1 and margin['max'] == 3
    assert margin['closest_windows'][0] == {'window_index': 0, 'time_range_s': [2., 4.],
                                           'label': 'negative', 'runner_up': 'positive', 'margin': 0.0}
    assert 'training-scope-detail' not in json.dumps(result)
    stored = store.load_artifact(result['artifact']['artifact_id'])
    assert stored['provenance']['params']['model']['limitations'] == model['limitations']
    full = json.loads(Path(export('json', stored['artifact_id'])['artifact']['uri']).read_text(encoding='utf-8'))
    assert full['model']['limitations'] == model['limitations']
