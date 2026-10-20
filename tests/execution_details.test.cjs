const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const ExecutionDetails=vm.runInNewContext(readFileSync(path.join(__dirname,'../frontend/bridge/execution-details.js'),'utf8')+'\nExecutionDetails');

test('effective preprocessing settings override requests and identify the selected trial',()=>{
  const html=ExecutionDetails.render({name:'preprocess',args:{source_id:'eeg_source',steps:[{op:'bandpass',low:1,high:50}]},result:{summary:{sample_rate_hz:64,processing_settings:[{op:'bandpass',low_hz:1,high_hz:31.5,method:'iir',order:4,phase:'zero'}]},artifact:{artifact_id:'art_signal',kind:'normalized_signal'}}},{manifests:[{manifest_id:'eeg_source',original_name:'s01.dat',trial_count:40,trial_index:0,trial_selection:'default_first',sample_rate_hz:128,unit:{value:'µV'}}]});
  assert.match(html,/第 1 \/ 40 个试次（仅默认首试次）/);
  assert.match(html,/1–31.5 Hz/);
  assert.doesNotMatch(html,/1–50 Hz|整文件完成|eeg_source/);
  assert.match(html,/输入采样率<\/dt><dd>128 Hz/);
  assert.match(html,/输出采样率<\/dt><dd>64 Hz/);
  assert.match(html,/data-execution-artifact="art_signal"/);
  const native=ExecutionDetails.render({name:'inspect_record',args:{manifest_id:'eeg_native'}},{manifests:[{manifest_id:'eeg_native',unit:{value:'dataset_native'}}]});
  assert.match(native,/原始数值（物理单位未指定）/);assert.doesNotMatch(native,/dataset_native/);
});

test('methods show recorded scientific values without inventing omitted defaults',()=>{
  const preparing=ExecutionDetails.render({name:'clean_artifacts',args:{method:'ica_iclabel'}});
  assert.match(preparing,/ICA \+ ICLabel/);
  assert.doesNotMatch(preparing,/0\.8|600|成分数|概率阈值/);
  const actual=ExecutionDetails.render({name:'clean_artifacts',args:{method:'ica_iclabel'},result:{summary:{method:'ica_iclabel',threshold:0.9,n_components:31,exclude_labels:['eye blink'],fit:{fit_seconds:120}}}});
  assert.match(actual,/概率阈值<\/dt><dd>0.9/);
  assert.match(actual,/拟合时长<\/dt><dd>120 s/);
  const features=ExecutionDetails.render({name:'extract_features',args:{windows_id:'art_windows',features:['bandpower']},result:{summary:{features:['bandpower'],bands:{alpha:[8,13]},amplitude_unit:'µV',power_unit:'µV²'}}},{artifacts:{art_windows:{summary:{window_s:4,stride_s:2}}}});
  assert.match(features,/窗长<\/dt><dd>4 s/);
  assert.match(features,/步长<\/dt><dd>2 s/);
  assert.match(features,/alpha 8–13 Hz/);
  assert.doesNotMatch(features,/art_windows|source_id/);
  const local=ExecutionDetails.render({name:'analyze_signal',args:{},result:{summary:{features:{features:['de'],bands:{alpha:[8,13]},de_bands:{alpha:[8,14]}}},artifacts:[{artifact_id:'art_windows',kind:'windows',analysis:{window_support:{window_s:4,stride_s:2,partial:'drop'}}}]}});
  assert.match(local,/窗长<\/dt><dd>4 s/);
  assert.match(local,/差分熵频带<\/dt><dd>alpha 8–14 Hz/);
});

test('actual outputs keep preview identities, deduplicate saved files and escape all text and attributes',()=>{
  const file={workspace_id:'ws_project',path:'results/a"b.csv',name:'<img onerror=alert(1)>',kind:'file'};
  const html=ExecutionDetails.render({name:'run_python',args:{code:'print("<script>")'},result:{code_path:'E:/scratch/run_1.py',stdout:'SECRET_STDOUT',artifacts:[{artifact_id:'art_real',kind:'plot',summary:{title:'<svg onload=alert(1)>'}}],workspace_file:file,workspace_files:[file]}});
  assert.equal((html.match(/data-execution-workspace=/g)||[]).length,1);
  assert.match(html,/data-execution-path="results\/a&quot;b.csv"/);
  assert.doesNotMatch(html,/<script>|<img|<svg|SECRET_STDOUT/);
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/data-execution-artifact="art_real"/);
  assert.match(html,/<details class="execution-code"><summary>查看执行脚本/);
  assert.doesNotMatch(html,/<details[^>]* open/);
});

test('file operations show paths and ranges without creating an analysis method',()=>{
  const html=ExecutionDetails.render({name:'read_file',args:{path:'protocol.md'},result:{start_line:1,end_line:20,content:'internal transcript'}});
  assert.match(html,/protocol.md/);
  assert.match(html,/第 1–20 行/);
  assert.doesNotMatch(html,/分析方法|internal transcript|参数|完整结果/);
  assert.equal(ExecutionDetails.render({name:'future_tool',description:'已经完成',args:{source_id:'hidden'},result:{stdout:'do not render'}}),'');
});

test('batch summaries report measured trials without projecting the first source as the whole batch',()=>{
  const html=ExecutionDetails.render({name:'workspace_quality',args:{path:'raw',pattern:'*.dat'},result:{summary:{measured:39,not_measured:1,excluded:['practice.dat']},artifact:{artifact_id:'art_batch',kind:'table',source_manifest_ids:['eeg_one','eeg_two']}}},{manifests:[{manifest_id:'eeg_one',original_name:'first.dat',trial_count:40,trial_index:0},{manifest_id:'eeg_two',original_name:'second.dat',trial_count:40,trial_index:0}]});
  assert.match(html,/已检查试次<\/dt><dd>39/);
  assert.match(html,/未完成检查的试次<\/dt><dd>1/);
  assert.match(html,/practice.dat/);
  assert.doesNotMatch(html,/first.dat|第 1|整文件完成/);
});
