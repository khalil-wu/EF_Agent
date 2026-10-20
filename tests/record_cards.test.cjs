const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('../frontend/node_modules/jsdom');

const source=readFileSync(path.join(__dirname,'../frontend/bridge/app.js'),'utf8');
const {document}=new JSDOM('').window;
const escape=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const runtime={document,esc:escape,qualitySummary:()=>'<div>前 10 秒的质量结果</div>',coordinateLabel:()=> 'Epoch 0 · -1–3 s'};
vm.runInNewContext(source.slice(source.indexOf('const SRC_LABEL='),source.indexOf('function addManifest(')),runtime);

function visibleText(node){
  if(node.nodeType===3)return node.textContent;
  if(node.tagName==='DETAILS'&&!node.open)return visibleText(node.querySelector('summary'));
  return [...node.childNodes].map(visibleText).join(' ');
}
function eeg(extra={}){
  return {manifest_id:'sig_example',modality:'eeg',original_name:'participant.dat',source_format:'dat',
    sample_rate_hz:128,n_channels:32,n_samples:8064,duration_s:63,signal_kind:'continuous',
    trial_count:40,trial_index:0,trial_selection:'default_first',trial_id:'trial_01',
    channel_names:['Fp1','AF3','F3'],unit:{value:'uV',source:'inferred'},
    channel_layout:{value:{Fp1:[0,1,0]},source:'user_confirmed'},reference:'unknown',
    format_profile:'deap_preprocessed',dataset:'unknown',
    metadata_evidence:{origin:{value:'unverified',source:'unknown'},sample_rate_hz:{value:128,source:'format_assumption'}},
    labels:{values:[9,5.03,7.13,6.62]},quality:{flat_channels:[],sample_count:1280},
    warnings:['结构与 DEAP 预处理格式兼容，不能确认官方来源或预处理历史。'],
    compat:[{model_id:'example-model',status:'incompatible',reasons:['需要重新采样']}],...extra};
}

test('record list presents file identity and selected-trial scope while technical reading details stay collapsed',()=>{
  const record=eeg(),card=runtime.renderManifest(record,{artifact_id:'wave_preview'}),visible=visibleText(card);
  assert.match(visible,/participant\.dat/);assert.match(visible,/DAT/);
  assert.match(visible,/32 通道/);assert.match(visible,/128 Hz/);assert.match(visible,/40 个试次/);
  assert.match(visible,/当前第 1 个试次（默认） · 该试次 63 s/);
  assert.doesNotMatch(visible,/Fp1|5\.03|DEAP|未核验|映射表|需要重新采样|前 10 秒/);
  assert.equal(card.querySelector('img'),null,'waveforms open only when the user requests the preview');
  assert.equal(card.querySelector('[data-open-attachment]').dataset.openAttachment,record.manifest_id);
  assert.equal(card.querySelector('.record-details').open,false);
  assert.equal(card.querySelector('.record-quality').open,false);
  assert.equal(card.querySelector('.record-quality').parentElement,card,'initial screening has its own collapsed section');
  card.querySelector('.record-details').open=true;
  assert.match(visibleText(card),/格式候选/);assert.match(visibleText(card),/未核验/);
  assert.doesNotMatch(visibleText(card),/5\.03|预处理历史|需要重新采样/,'longer labels, notes and model requirements remain separate disclosures');
});

test('explicit trial selection and epoch totals retain their actual meaning',()=>{
  const selected=runtime.renderManifest(eeg({trial_index:7,trial_selection:'explicit'}));
  assert.match(visibleText(selected),/当前第 8 个试次 · 该试次 63 s/);
  assert.doesNotMatch(visibleText(selected),/默认/);
  const epochs=runtime.renderManifest(eeg({signal_kind:'epochs',duration_s:12,n_samples:1536,n_segments:3,
    segments:[{epoch_index:0,epoch_tmin_s:-1,start_sample:0,end_sample:512}],trial_count:1,trial_index:0}));
  assert.match(visibleText(epochs),/3 个独立 Epoch · 累计采样 12 s/);
  assert.doesNotMatch(visibleText(epochs),/时长|连续记录/);
  epochs.querySelector('.record-details').open=true;
  assert.match(visibleText(epochs),/Epoch 之间不连续/);
});

test('missing sampling rate and physical unit produce one actionable notice without alarming on format assumptions',()=>{
  const missing=runtime.renderManifest(eeg({sample_rate_hz:null,duration_s:null,unit:{value:'unknown',source:'unknown'}}));
  assert.equal(missing.querySelectorAll('.record-notice').length,1);
  assert.match(visibleText(missing),/采样率、物理单位待补充/);
  assert.match(visibleText(missing),/时间和幅值的分析时需要确认/);
  assert.match(visibleText(missing),/8064 点/);
  assert.doesNotMatch(visibleText(missing),/DEAP|来源|格式假设|warning|未核验/);
  assert.equal(runtime.renderManifest(eeg()).querySelector('.record-notice'),null,'estimated or assumed known values do not create a generic warning');
});

test('all manifest data including unfamiliar metadata is still inspectable without changing source values',()=>{
  const record=eeg({extension_metadata:{device:'headset <prototype>',calibration:null,channels:['F3','F4']}});
  const card=runtime.renderManifest(record);
  assert.deepEqual(JSON.parse(card.querySelector('.record-raw pre').textContent),record);
  assert.match(card.querySelector('.record-details').textContent,/格式假设/);
  assert.match(card.querySelector('.record-details').textContent,/结构与 DEAP/);
  assert.match(card.querySelector('.record-details').textContent,/需要重新采样/);
  assert.equal(card.querySelector('prototype'),null);
});

test('video and document cards keep detailed interpretation outside their default summary',()=>{
  const video=runtime.renderManifest({manifest_id:'vid_one',original_name:'face.mp4',source_format:'mp4',modality:'video',
    duration_s:63,width:1920,height:1080,fps:30,role:{value:'face'},sync:{start_offset_s:2,source:'user_confirmed'}});
  assert.match(visibleText(video),/63 s/);assert.match(visibleText(video),/1920 × 1080/);
  assert.doesNotMatch(visibleText(video),/面部记录|已确认同步|原 EEG/);
  const document=runtime.renderManifest({manifest_id:'doc_one',original_name:'protocol.pdf',source_format:'pdf',modality:'document',
    page_count:12,size_bytes:4096,document_index:{status:'ready',n_chunks:26}});
  assert.match(visibleText(document),/12 页/);assert.match(visibleText(document),/4 KB/);
  assert.doesNotMatch(visibleText(document),/26 个片段|已解析/);
  document.querySelector('.record-details').open=true;
  assert.match(visibleText(document),/26 个片段/);
});
