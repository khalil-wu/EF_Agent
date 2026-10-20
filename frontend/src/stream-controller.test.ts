import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../bridge/app.js',import.meta.url),'utf8');
const controller=source.slice(source.indexOf('function renderSession('),source.indexOf('async function watchSession('));
function shell(){
  const applied:any[]=[],traces:any[]=[],status:boolean[]=[];
  const context=vm.createContext({
    liveCursor:null,streaming:true,streamController:{},requestFailure:'',viewVersion:1,
    transcript:{apply:(event:any)=>applied.push(event),hydrate(){}},eventTraces:new Map(),workspaceBrowser:{load:async()=>{},refresh:async()=>{}},
    $:()=>({textContent:'',hidden:false}),resetView(){},setConversationModel(){},setActiveSkill(){},renderResultCatalog(){},renderInputRequest(){},autosize(){},setConn(){},
    traceEv:(...args:any[])=>traces.push(args),restoreStatus:()=>status.push(context.streaming),
    refreshSessions:async()=>{},reportError(){},TextDecoder,
  });
  vm.runInContext(controller,context);
  return {context,applied,traces,status};
}
const encode=(event:any)=>new TextEncoder().encode('data: '+JSON.stringify(event)+'\n\n');

test('generic emotion result keeps paired coordinates and scores while model identity stays in source details',()=>{
  const context=vm.createContext({esc:(x:any)=>String(x),MODEL_TASK_LABEL:{emotion:'情绪识别'},coverageSummary:()=>''});
  vm.runInContext(source.slice(source.indexOf('function coordinateLabel('),source.indexOf('function qualitySummary(')),context);
  vm.runInContext(source.slice(source.indexOf('function predictionSummary('),source.indexOf('function openArtifact(')),context);
  const html=context.predictionSummary({model_id:'camer_eav_photo_fixed_fold0',model_version:'test',task:'emotion',
    output_kind:'classification',prediction_count:1,score_kind:'logits',calibration:{status:'unknown'},labels:['neutral','happy'],
    paired_video:{sync_offset_s:20},prediction_preview:[{window_index:0,original_time_range_s:[20,25],
      video_time_range_s:[0,5],label:'happy',scores:[-2,1]}]});
  assert.match(html,/使用脑电与配对视频/);assert.match(html,/原记录 20–25 s/);assert.match(html,/视频 0–5 s/);
  assert.match(html,/<b>neutral<\/b>[\s\S]*?<em>-2\.000<\/em>/);assert.match(html,/类别 logits/);assert.doesNotMatch(html,/类别概率/);
  assert.doesNotMatch(html,/CAMER|camer_eav|联合识别/);
});

test('recognition progress is phrased as a user task rather than a model selection command',()=>{
  const context=vm.createContext({});
  vm.runInContext(source.slice(source.indexOf('function toolResultSummary('),source.indexOf('/* ---------- manifests ---------- */')),context);
  const ready=context.toolResultSummary('route_model',{selected_model:'camer_eav_photo_fixed_fold0'});
  const saved=context.toolResultSummary('run_model',{summary:{prediction_count:4}});
  assert.match(ready,/适用的识别方法/);assert.match(saved,/4 个窗口/);
  assert.doesNotMatch(ready+saved,/camer|CAMER|联合识别/);
});

test('an SSE model error releases the composer state while the save event is still pending',()=>{
  const {context,applied,status}=shell();
  context.consumeEngineEvent({type:'error',text:'远端关闭连接',run_id:'run_failure',sequence:4});
  assert.equal(context.streaming,false);
  assert.equal(context.requestFailure,'远端关闭连接');
  assert.deepEqual(status,[false]);
  assert.equal(applied[0].type,'error');
  context.consumeEngineEvent({type:'session_saved',run_id:'run_failure',sequence:5});
  assert.equal(context.streamController,null);
  assert.equal(context.requestFailure,'远端关闭连接');
});

test('restoring a snapshot during persistence applies its live failure after the running checkpoint',()=>{
  const {context}=shell();
  context.renderSession({running:true,last_error:'',live:{run_id:'run_failure',sequence:4,events:[{type:'error',text:'远端关闭连接',run_id:'run_failure',sequence:4}]}});
  assert.equal(context.streaming,false);
  assert.equal(context.requestFailure,'远端关闭连接');
  assert.equal(context.liveCursor.sequence,4);
});

test('an SSE error followed by EOF keeps the concrete model failure instead of inventing a transport interruption',async()=>{
  const {context}=shell();
  let reads=0;
  const response={body:{getReader:()=>({read:async()=>reads++?{done:true}:{done:false,value:encode({type:'error',text:'远端关闭连接',run_id:'run_failure',sequence:4})}})}};
  await context.consumeEventStream(response,1);
  assert.equal(context.requestFailure,'远端关闭连接');
  assert.equal(context.streaming,false);
});

test('session_saved completes the SSE consumer without waiting for the TCP stream to close',async()=>{
  const {context}=shell();
  let cancelled=false,reads=0;
  const response={body:{getReader:()=>({read:async()=>{reads++;return {done:false,value:encode({type:'session_saved',run_id:'run_success',sequence:7})}},cancel:async()=>{cancelled=true}})}};
  await context.consumeEventStream(response,1);
  assert.equal(reads,1);
  assert.equal(cancelled,true);
  assert.equal(context.streaming,false);
});

test('an SSE EOF without a task terminal event remains a connection failure',async()=>{
  const {context}=shell();
  const response={body:{getReader:()=>({read:async()=>({done:true})})}};
  await assert.rejects(context.consumeEventStream(response,1),/实时连接已中断/);
});
