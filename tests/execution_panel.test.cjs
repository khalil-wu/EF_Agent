const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {JSDOM}=require('../frontend/node_modules/jsdom');
const source=readFileSync(path.join(__dirname,'../frontend/bridge/app.js'),'utf8');
const ExecutionDetails=vm.runInNewContext(readFileSync(path.join(__dirname,'../frontend/bridge/execution-details.js'),'utf8')+'\nExecutionDetails');

test('a selected step shows its method and outputs with all raw technical details collapsed',()=>{
  const {document}=new JSDOM('<div class="sheet-body"><div id="noTrace"></div><div id="trace"></div></div>').window;
  const panels=[];
  const runtime={document,$:selector=>document.querySelector(selector),ExecutionDetails,
    esc:value=>String(value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])),
    manifests:[],artifacts:{},eventTraces:new Map(),showTabAndOpen:name=>panels.push(name),toolResultSummary:()=> '已生成清洗信号'};
  vm.runInNewContext(source.slice(source.indexOf('function traceEv('),source.indexOf('function toolResultSummary(')),runtime);
  const other=runtime.traceEv('ok','读取文件','protocol.md');
  const selected=runtime.traceEv('run','预处理','滤波',{steps:[{op:'bandpass',low:1,high:40}]});
  selected.toolName='preprocess';runtime.eventTraces.set('filter',selected);
  runtime.openExecutionDetails('filter');
  assert.deepEqual(panels,['trace']);
  assert.equal(selected.dataset.executionFocused,'true');
  assert.equal(other.hasAttribute('data-execution-focused'),false);
  assert.match(selected.querySelector('.execution-overview').textContent,/带通滤波.*1–40 Hz/);
  assert.equal(selected.querySelector('.execution-tech').open,false);
  assert.equal(selected.querySelector('.execution-tech details').open,false);
  assert.match(selected.querySelector('.execution-tech summary').textContent,/技术详情/);
  selected.className='ev ok';selected.querySelector('.st').textContent='完成';
  runtime.traceDetail(selected,'完整结果',{artifact:{artifact_id:'art_clean',kind:'normalized_signal'},summary:{sample_rate_hz:128}});
  assert.equal(selected.querySelectorAll('.execution-overview').length,1);
  assert.equal(selected.querySelector('[data-execution-artifact]').dataset.executionArtifact,'art_clean');
  assert.equal(selected.querySelector('.execution-tech').open,false,'completion must not expand the technical payload');
  assert.equal(document.querySelectorAll('.ev').length,2,'opening details does not recreate or lose execution history');
});

test('execution details preserve the active panel without needing a top-level trace tab',()=>{
  const {document}=new JSDOM('<button id="detailBack" hidden></button><div id="panelTitle"></div><div id="trace"></div><div class="tabs"><button class="tab" data-tab="data"></button><button class="tab" data-tab="art"></button><button class="tab" data-tab="know"></button></div><div class="pane" id="pane-data"></div><div class="pane" id="pane-art"></div><div class="pane" id="pane-know"></div><div class="pane" id="pane-trace"></div>').window;
  const runtime={document,$:selector=>document.querySelector(selector),$$:selector=>[...document.querySelectorAll(selector)],resetAttachmentView(){},currentPanel:'art',lastContentPanel:'data'};
  vm.runInNewContext(source.slice(source.indexOf('function showTab('),source.indexOf('function badge(')),runtime);
  runtime.showTab('trace');
  assert.equal(runtime.lastContentPanel,'art');
  assert.equal(document.querySelector('.tab.on'),null);
  assert.equal(document.querySelector('#pane-trace').classList.contains('on'),true);
  assert.equal(document.querySelector('#detailBack').hidden,false);
  assert.equal(document.querySelector('#detailBack').getAttribute('aria-label'),'返回结果');
  runtime.showTab(runtime.currentPanel);
  assert.equal(runtime.lastContentPanel,'art','opening the sheet twice retains the return destination');
  runtime.showTab(runtime.lastContentPanel);
  assert.equal(document.querySelector('.tab.on').dataset.tab,'art');
  assert.equal(document.querySelector('#detailBack').hidden,true);
});
