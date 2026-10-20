"""Synthetic replies test paired clocks/storage; real accuracy is never inferred here."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.io import savemat

from eegagent.worker import remote_model, signals, store
from eegagent.worker.channel_orders import EAV_CHANNEL_ORDER
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.models import route_model
from eegagent.worker.outputs import export, read_artifact
from eegagent.worker.registry import load_registry
from eegagent.worker.tools import run_tool

MODEL = 'camer_eav_photo_fixed_fold0'


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv('EEGAGENT_STORAGE_DIR', str(tmp_path))
    signals._CACHE.clear()
    folder = store.ensure_layout() / 'uploads'
    data = np.random.default_rng(12).normal(size=(30, 10000))
    path = folder / 'eav.npz'
    np.savez(path, data=data, fs=500, ch_names=EAV_CHANNEL_ORDER, unit='dataset_native',
             dataset='EAV', format_profile='eav_raw_trial', reference='mastoids', task_mode='speaking')
    eeg = inspect_record(str(path))
    video_path = folder / 'participant.mp4'
    video_path.write_bytes(b'explicit test fixture; transport mocked, not actual video')
    video = {'manifest_id':'vid_fixture', 'modality':'video', 'source_path':str(video_path),
             'attachment_sha256':'test-only', 'duration_s':21.033, 'timing':{'status':'cfr_verified'}, 'role':{'value':'face','source':'user_confirmed'},
             'sync':{'clock':'eeg_original','start_offset_s':0,'source':'user_confirmed'}}
    store.save_manifest(video)
    return eeg, video


def test_paired_route_needs_context_and_does_not_silently_select_eeg_only(pair):
    eeg, video = pair
    assert route_model('emotion', eeg, video)['selected_model'] == MODEL
    unknown = {**video, 'sync':None}
    route = route_model('emotion', eeg, unknown)
    assert route['selected_model'] is None
    assert next(row for row in route['candidates'] if row['model_id']==MODEL)['route_status']=='needs_context'
    for args in ({}, {'video_manifest_id':'vid_fixture','video_role':'stimulus'}):
        with pytest.raises(ValueError, match='视频'):
            run_tool('run_model', {'source_id':eeg['manifest_id'],'model_id':MODEL, **args})


def test_camer_tensor_prediction_source_clocks_and_exports(pair, monkeypatch):
    eeg, video = pair
    eos = np.arange(4*32*5,dtype=np.float32).reshape(4,32,5)
    images = np.zeros((4,6,224,224,3),dtype=np.float32)
    logits = np.array([[0,1,2,3,4]]*4,dtype=np.float32)

    def infer(request,response,model,*_):
        with np.load(request,allow_pickle=False) as archive:
            metadata=json.loads(archive['metadata'].item())
        assert metadata['video_manifest_id']=='vid_fixture' and metadata['sync_offset_s']==0
        assert metadata['windows'][2]['video_start_s']==10 and metadata['windows'][2]['video_end_s']==15
        output={'model':{'model_id':MODEL,'model_version':model['model_version'],'task':'emotion',
                         'weight_sha256':model['sha256'],'input_recipe':{'test':'synthetic'}},
                'output':{'kind':'classification','labels':model['label_map'],'score_kind':'logits'},
                'preparation':{},'runtime':{'device':'synthetic, not a GPU experiment'},
                'video_preparation':{'frame_times_ms':[[0,750,1750,2750,3750,4750]]*4}}
        np.savez(response,eeg_inputs=eos,video_inputs=images,logits=logits,metadata=json.dumps(output))
    monkeypatch.setattr(remote_model,'_remote_infer',infer)
    result=run_tool('run_model',{'source_id':eeg['manifest_id'],'model_id':MODEL,'video_manifest_id':'vid_fixture'})
    prediction=store.load_artifact(result['artifact']['artifact_id'])
    assert prediction['source_manifest_ids']==[eeg['manifest_id'],'vid_fixture']
    rows=read_artifact(prediction['artifact_id'],'predictions')['rows']
    assert rows[2]['video_time_range_s']==[10,15] and rows[2]['scores']==[0,1,2,3,4]
    inputs=store.load_artifact(result['input_artifact_id'])
    assert len(inputs['summary']['source_channel_order'])==30 and len(inputs['summary']['model_channel_order'])==32
    with np.load(inputs['uri']) as archive:
        np.testing.assert_array_equal(archive['eeg_inputs'],eos)
        np.testing.assert_array_equal(archive['video_inputs'],images)
    assert read_artifact(inputs['artifact_id'],'eeg_inputs',limit=1)['rows'][0]['shape']==[32,5]
    assert read_artifact(inputs['artifact_id'],'video_inputs',limit=1)['rows'][0]['shape']==[6,224,224,3]
    exported=export('csv',prediction['artifact_id'])['artifact']
    csv_rows=list(csv.DictReader(Path(exported['uri']).open(encoding='utf-8')))
    assert csv_rows[2]['video_manifest_id']=='vid_fixture' and csv_rows[2]['video_start_s']=='10.0'
    assert csv_rows[2]['sync_offset_s']=='0' and csv_rows[2]['video_role']=='face'


def test_epoch_pairing_uses_original_trial_axis_and_checks_all_windows(pair):
    eeg, video = pair
    path=Path(eeg['source_path']).with_name('subject1_eeg.mat')
    raw=np.load(eeg['source_path'])['data']
    savemat(path,{'seg':np.stack([raw.T,raw.T],axis=2)})
    labels=np.zeros((10,2)); labels[1,:]=1
    savemat(path.with_name('subject1_eeg_label.mat'),{'label':labels})
    epoch=inspect_record(str(path),trial_index=1)
    epoch=update_record_metadata(epoch['manifest_id'],preprocessing='eav_raw')['manifest']
    assert route_model('emotion',epoch,video,model_id=MODEL)['selected_model'] is None
    updated=update_record_metadata('vid_fixture',sync_offset_s=20)['manifest']
    assert route_model('emotion',epoch,updated,model_id=MODEL)['selected_model']==MODEL
    config={**load_registry()[MODEL],'model_id':MODEL}
    context={'video_manifest_id':updated['manifest_id'],'duration_s':21.033,'sync_offset_s':20}
    _,_,windows=remote_model._prediction_windows(epoch['manifest_id'],config,1,None,None,context)
    row=read_artifact(windows['artifact_id'],'windows')['rows'][0]
    assert row['original_time_range_s']==[20,25] and row['video_time_range_s']==[0,5]
    with pytest.raises(ValueError,match='视频'):
        remote_model._prediction_windows(epoch['manifest_id'],config,1,None,None,{**context,'duration_s':12})


# Inference is mocked; use isolated registered contracts rather than local weights.
pytestmark = pytest.mark.usefixtures("registered_model_registry")
