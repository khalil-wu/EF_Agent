"""Verify complete DEAP context, per-second clocks, and exact response persistence; no accuracy claims."""
import csv
import json
import pickle
from pathlib import Path
import numpy as np
import pytest

from eegagent.worker import remote_model, signals, store
from eegagent.worker.channel_orders import DEAP_MAHNOB_CHANNEL_ORDER
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.models import route_model
from eegagent.worker.outputs import read_artifact, export
from eegagent.worker.registry import load_registry
from eegagent.worker.tools import run_tool

MODEL='camer_deap_fixed_1s_fold0'


@pytest.fixture
def pair(tmp_path,monkeypatch):
    monkeypatch.setenv('EEGAGENT_STORAGE_DIR',str(tmp_path));signals._CACHE.clear()
    raw=np.random.default_rng(10).normal(size=(32,8064))
    path=store.ensure_layout()/'uploads'/'trial.npz'
    np.savez(path,data=raw,fs=128,ch_names=DEAP_MAHNOB_CHANNEL_ORDER,unit='dataset_native',dataset='DEAP',
             format_profile='deap_preprocessed_trial',reference='average',preprocessing='deap_official_preprocessed')
    eeg=inspect_record(str(path))
    video_path=path.with_suffix('.avi');video_path.write_bytes(b'explicit synthetic transport fixture')
    video={'manifest_id':'vid_deap','modality':'video','source_path':str(video_path),'attachment_sha256':'fixture',
           'duration_s':60.,'timing':{'status':'cfr_verified'},'role':{'value':'face','source':'user_confirmed'},
           'sync':{'clock':'eeg_original','start_offset_s':3,'source':'user_confirmed'}}
    store.save_manifest(video)
    return eeg,video,raw


def test_sixty_second_stimulus_is_covered_without_predicting_baseline(pair):
    eeg,video,raw=pair
    assert route_model('emotion',eeg,video)['selected_model']==MODEL
    bad={**video,'sync':{**video['sync'],'start_offset_s':0}}
    assert route_model('emotion',eeg,bad,model_id=MODEL)['selected_model'] is None
    config={**load_registry()[MODEL],'model_id':MODEL}
    context={'video_manifest_id':'vid_deap','duration_s':60,'sync_offset_s':3}
    trial,payload,windows=remote_model._prediction_windows(eeg['manifest_id'],config,1,None,None,context)
    assert payload['windows'][0]['start_sample']==0 and payload['windows'][0]['end_sample']==8064
    rows=read_artifact(windows['artifact_id'],'windows',limit=100)['rows']
    assert len(rows)==60 and rows[0]['start_s']==3 and rows[-1]['end_s']==63
    assert rows[0]['video_time_range_s']==[0,1] and rows[-1]['video_time_range_s']==[59,60]
    assert windows['time_range_s']==[3,63] and trial['time_range_s']==[0,63]
    with pytest.raises(ValueError,match='基线'):
        remote_model._prediction_windows(eeg['manifest_id'],config,1,0,2,context)


def test_partial_prediction_sends_whole_waveform_and_preserves_four_frame_response(pair,monkeypatch):
    eeg,video,raw=pair
    eeg_inputs=np.arange(2*32*5,dtype=np.float32).reshape(2,32,5)
    video_inputs=np.ones((2,4,224,224,3),dtype=np.float32)*.4
    logits=np.array([[0,1,4,2],[3,0,1,2]],dtype=np.float32)
    def infer(request,response,model,*_):
        with np.load(request) as archive:
            np.testing.assert_array_equal(archive['signal'],raw)
            metadata=json.loads(archive['metadata'].item())
        assert metadata['input_stage']=='deap_preprocessed_trial'
        assert metadata['windows'][0]['start_sample']==5*128 and metadata['windows'][0]['video_start_s']==2
        identity={'model_id':MODEL,'model_version':model['model_version'],'task':'emotion','weight_sha256':model['sha256'],'input_recipe':{}}
        meta={'model':identity,'output':{'kind':'classification','labels':model['label_map'],'score_kind':'logits'},
              'preparation':{},'runtime':{'device':'synthetic'},'video_preparation':{'frame_times_ms':[[2000,2260,2520,2780],[3180,3440,3700,3980]]}}
        np.savez(response,eeg_inputs=eeg_inputs,video_inputs=video_inputs,logits=logits,metadata=json.dumps(meta))
    monkeypatch.setattr(remote_model,'_remote_infer',infer)
    result=run_tool('run_model',{'source_id':eeg['manifest_id'],'model_id':MODEL,'video_manifest_id':'vid_deap','start_s':5,'end_s':7})
    assert result['summary']['prediction_count']==2 and result['summary']['coverage']['complete']
    assert result['summary']['prediction_preview'][0]['label']=='HVLA'
    first = result['summary']['prediction_preview'][0]
    assert first['label_text'] == '高效价、低唤醒' and first['valence'] == 'high' and first['arousal'] == 'low'
    assert result['result_links'][0]['uri'] == 'artifact://' + result['artifact']['artifact_id']
    record=store.load_artifact(result['input_artifact_id'])
    assert len(record['summary']['model_channel_order'])==32
    assert read_artifact(record['artifact_id'],'video_inputs',limit=1)['frames_per_window']==4
    with np.load(record['uri']) as archive:
        np.testing.assert_array_equal(archive['eeg_inputs'],eeg_inputs)
        np.testing.assert_array_equal(archive['video_inputs'],video_inputs)
    csv_art=export('csv',result['artifact']['artifact_id'])['artifact']
    rows=list(csv.DictReader(Path(csv_art['uri']).open(encoding='utf-8')))
    assert rows[0]['start_s']=='5.0' and rows[0]['video_start_s']=='2.0' and rows[0]['sync_offset_s']=='3'
    assert rows[0]['label_text']=='高效价、低唤醒' and rows[0]['valence']=='high' and rows[0]['arousal']=='low'


def test_preprocessed_contract_cannot_be_inferred_from_array_shape(pair):
    eeg,video,raw=pair
    forged={**eeg,'metadata_evidence':{**eeg['metadata_evidence'],'preprocessing':{'value':'unknown','source':'unknown'}}}
    assert route_model('emotion',forged,video,model_id=MODEL)['selected_model'] is None


def test_dat_requires_confirmation_then_keeps_native_waveform_and_known_format_conventions(pair):
    eeg,video,raw=pair
    path=Path(eeg['source_path']).with_name('synthetic_deap.dat')
    data=np.concatenate([raw,np.zeros((8,8064))])[None]
    with path.open('wb') as file:
        pickle.dump({'data':data},file)
    initial=inspect_record(str(path))
    assert initial['sample_rate_hz']==128 and initial['dataset']=='unknown'
    assert initial['metadata_evidence']['sample_rate_hz']['source']=='format_assumption'
    assert route_model('emotion',initial,video)['selected_model'] is None
    confirmed=update_record_metadata(initial['manifest_id'],preprocessing='deap_official_preprocessed')['manifest']
    assert confirmed['sample_rate_hz']==128 and confirmed['channel_names']==DEAP_MAHNOB_CHANNEL_ORDER
    assert confirmed['unit']['value']=='dataset_native' and confirmed['reference']=='average'
    assert confirmed['dataset']=='DEAP' and confirmed['metadata_evidence']['origin']['value']=='unverified'
    assert route_model('emotion',confirmed,video)['selected_model']==MODEL
    np.testing.assert_array_equal(signals.load_signal(confirmed['manifest_id'])[0],raw)


# Inference is mocked; use isolated registered contracts rather than local weights.
pytestmark = pytest.mark.usefixtures("registered_model_registry")
