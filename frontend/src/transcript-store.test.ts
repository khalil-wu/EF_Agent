import test from 'node:test';
import assert from 'node:assert/strict';
import {createTranscriptStore,type Event,type Message,type Tool,type Turn} from './transcript-store';

type Store=ReturnType<typeof createTranscriptStore>;
const turns=(store:Store)=>store.getSnapshot().rows.filter((row):row is Turn=>row.type==='turn');
const entry=(store:Store,id:string)=>turns(store).flatMap(row=>row.entries).find(item=>item.id===id)!;
const replay=(store:Store,events:Event[])=>events.forEach(store.apply);

test('provider reasoning stays transient, follows its message across steering and ignores expired replay',()=>{
  const store=createTranscriptStore(),ts=new Date().toISOString();
  store.addTurn('first');store.apply({type:'message_started',message_id:'reason-first'});
  const before=store.getSnapshot().contentVersion;
  store.apply({type:'reasoning_delta',message_id:'reason-first',text:'先核对通道。',ts});
  store.apply({type:'reasoning_delta',message_id:'reason-first',text:'再检查工频。',ts});
  assert.equal(turns(store)[0].reasoning?.text,'先核对通道。再检查工频。');
  assert.equal((entry(store,'reason-first') as Message).text,'','reasoning is not conversation prose');
  assert.equal(store.getSnapshot().contentVersion,before,'transient progress does not request page scrolling');
  store.addUser({text:'另一个问题'});store.addTurn('second');
  store.apply({type:'message_started',message_id:'reason-second'});
  store.apply({type:'reasoning_delta',message_id:'reason-first',text:'旧轮补充。',ts});
  assert.match(turns(store)[0].reasoning!.text,/旧轮补充。$/);
  assert.equal(turns(store)[1].reasoning,undefined);
  store.apply({type:'reasoning_delta',message_id:'reason-second',text:'过期内容',ts:new Date(Date.now()-10000).toISOString()});
  assert.equal(turns(store)[1].reasoning,undefined);
  store.apply({type:'reasoning_delta',message_id:'reason-second',text:'新的推理。'.repeat(300),ts});
  assert.ok(turns(store)[1].reasoning!.text.length<1000,'a long trace has a bounded recent preview');
  store.hydrate({messages:[{role:'assistant',content:'最终结果',message_id:'saved'}],running:false});
  assert.equal(turns(store)[0].reasoning,undefined,'durable history does not restore transient text');
});

test('a rejected truncated call survives live replay and history hydration without parsing its raw text',()=>{
  const raw='{"code":"'+'x'.repeat(3177),error='模型返回的工具参数不完整，本次未执行。';
  const rejected={status:'error',error:{code:'bad_args',message:error}};
  const live=createTranscriptStore();
  replay(live,[{type:'agent_start'},{type:'tool_call_started',tool_call_id:'bad',tool_name:'run_python'},
    {type:'tool_execution_end',tool_call_id:'bad',tool_name:'run_python',is_error:true,result:rejected,arguments_error:error,raw_arguments:raw}]);
  assert.equal((entry(live,'bad') as Tool).status,'error');
  live.hydrate({running:false,messages:[{role:'user',content:'处理 EEG'},
    {role:'assistant',tool_calls:[{id:'bad',arguments_error:error,function:{name:'run_python',arguments:raw}}]},
    {role:'tool',tool_call_id:'bad',content:JSON.stringify(rejected)},
    {role:'assistant',content:'已重新生成正确参数并完成。'}]});
  assert.equal((entry(live,'bad') as Tool).status,'error');
  assert.match((turns(live).at(-1)!.entries.at(-1) as Message).text,/已重新生成/);
});

test('selected file and directory references keep their actual identities after hydration',()=>{
  const store=createTranscriptStore();
  const references=[{workspace_id:'ws_123456abcdef',path:'.',name:'当前文件夹',kind:'directory'},{manifest_id:'sig_123456',name:'record.npz',kind:'file'}];
  store.hydrate({messages:[{role:'user',content:'分析 @.\n<file_references>internal</file_references>',presentation:{text:'分析 @.',references}}],running:false});
  const user=store.getSnapshot().rows[0];assert.equal(user.type,'user');if(user.type!=='user')throw new Error('missing user');
  assert.equal(user.text,'分析 @.');assert.deepEqual(user.references,references);
});

test('refresh restores output cards from compact tool result links into the original turn',()=>{
  const store=createTranscriptStore();
  store.hydrate({messages:[
    {role:'user',content:'本地分析文件'},
    {role:'assistant',content:'开始分析',tool_calls:[{id:'call_local',function:{name:'analyze_signal',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'call_local',content:JSON.stringify({status:'ok',summary:{n_windows:4},result_links:[{artifact_id:'art_plot',kind:'plot'},{artifact_id:'art_csv',kind:'table'}]})},
    {role:'assistant',content:'结果已保存'},
    {role:'user',content:'继续分析第二个文件'},
    {role:'assistant',content:'第二个结果已保存',tool_calls:[{id:'call_second',function:{name:'analyze_signal',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'call_second',content:JSON.stringify({status:'ok',artifacts:[{artifact_id:'art_second'}],result_links:[{artifact_id:'art_second'}]})},
  ],running:false});
  assert.deepEqual(turns(store)[0].artifacts,['art_plot','art_csv']);
  assert.deepEqual(turns(store)[1].artifacts,['art_second']);
});

test('history uses the complete tool presentation while the model content keeps its bounded preview',()=>{
  const store=createTranscriptStore();
  const files=Array.from({length:41},(_,index)=>({workspace_id:'ws',path:index===40?'analysis/summary.csv':`analysis/trial_${index+1}.npy`,name:index===40?'summary.csv':`trial_${index+1}.npy`,kind:'file'}));
  const bounded=JSON.stringify({status:'ok',workspace_files:files.slice(0,8),workspace_files_count:41,workspace_files_omitted:true});
  store.hydrate({messages:[{role:'user',content:'批量清洗并保存'},
    {role:'assistant',tool_calls:[{id:'batch-files',function:{name:'run_python',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'batch-files',content:bounded,tool_presentation:{workspace_files:files,result_links:[{artifact_id:'art_complete',kind:'table'}]}},
    {role:'assistant',content:'41个文件已保存。'}],running:false});
  const restored=entry(store,'batch-files') as Tool;
  assert.equal(restored.result.workspace_files.length,41);
  assert.equal(restored.result.workspace_files.at(-1).path,'analysis/summary.csv');
  assert.deepEqual(turns(store)[0].artifacts,['art_complete']);
  assert.equal(JSON.parse(bounded).workspace_files.length,8,'the UI overlay does not rewrite model history');
});

test('history keeps first artifact ownership when later tools read or reuse prior results',()=>{
  const store=createTranscriptStore();
  store.hydrate({messages:[
    {role:'user',content:'清洗并画图'},
    {role:'assistant',tool_calls:[{id:'clean',function:{name:'clean_artifacts',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'clean',content:JSON.stringify({status:'ok',artifact:{artifact_id:'art_signal'},result_links:[{artifact_id:'art_plot'}]})},
    {role:'user',content:'解释已有结果'},
    {role:'assistant',tool_calls:[{id:'read',function:{name:'read_artifact',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'read',content:JSON.stringify({status:'ok',artifact:{artifact_id:'art_plot',summary:{title:'已有图表'}}})},
    {role:'assistant',tool_calls:[{id:'read_unassigned',function:{name:'read_artifact',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'read_unassigned',content:JSON.stringify({status:'ok',artifact:{artifact_id:'art_before_history'}})},
    {role:'user',content:'沿用清洗结果，批量处理'},
    {role:'assistant',tool_calls:[{id:'batch',function:{name:'run_python',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'batch',content:JSON.stringify({status:'ok',artifacts:[{artifact_id:'art_signal'},{artifact_id:'art_second_signal'}],result_links:[{artifact_id:'art_plot'},{artifact_id:'art_second_signal'},{artifact_id:'art_second_plot'}]})},
  ],running:false});
  assert.deepEqual(turns(store).map(row=>row.artifacts),[['art_signal','art_plot'],[],['art_second_signal','art_second_plot']]);
  assert.equal((entry(store,'read') as Tool).result.artifact.summary.title,'已有图表','reading still retains the source record in the execution detail');
});

test('live artifact events keep first ownership and read_artifact never creates a new output',()=>{
  const store=createTranscriptStore();
  store.addUser({text:'绘制功率谱'});
  replay(store,[
    {type:'tool_execution_start',tool_call_id:'make',tool_name:'plot',args:{}},
    {type:'artifact_ready',tool_call_id:'make',tool_name:'plot',result:{artifact_id:'art_old'}},
    {type:'session_saved'},
  ]);
  store.addUser({text:'读取此前结果'});
  replay(store,[
    {type:'tool_execution_start',tool_call_id:'read',tool_name:'read_artifact',args:{artifact_id:'art_old'}},
    {type:'artifact_ready',tool_call_id:'read',tool_name:'read_artifact',result:{artifact_id:'art_old'}},
    {type:'artifact_ready',tool_call_id:'read',tool_name:'read_artifact',result:{artifact_id:'art_before_history'}},
    {type:'session_saved'},
  ]);
  store.addUser({text:'复用结果生成新图'});
  replay(store,[
    {type:'tool_execution_start',tool_call_id:'batch',tool_name:'run_python',args:{}},
    {type:'artifact_ready',tool_call_id:'batch',tool_name:'run_python',result:{artifact_id:'art_old'}},
    {type:'artifact_ready',tool_call_id:'batch',tool_name:'run_python',result:{artifact_id:'art_new'}},
  ]);
  assert.deepEqual(turns(store).map(row=>row.artifacts),[['art_old'],[],['art_new']]);
});

test('a model failure immediately ends partial text and unfinished tools before persistence completes',()=>{
  const store=createTranscriptStore();
  store.addUser({text:'分析此数据。'});
  replay(store,[
    {type:'agent_start'},
    {type:'message_started',message_id:'msg_partial'},
    {type:'message_delta',message_id:'msg_partial',text:'已读取信号'},
    {type:'tool_call_started',message_id:'msg_partial',tool_call_id:'call_pending',tool_name:'plot'},
    {type:'error',text:'上游连接被关闭'},
  ]);
  const failed=turns(store)[0];
  assert.equal(failed.running,false);
  assert.equal(failed.error,'上游连接被关闭');
  assert.deepEqual(entry(store,'msg_partial'),{type:'message',id:'msg_partial',text:'已读取信号',phase:'partial',done:true});
  assert.equal((entry(store,'call_pending') as Tool).status,'interrupted');
  replay(store,[{type:'session_saved'}]);
  assert.equal(turns(store).length,1);
  assert.equal(turns(store)[0].error,'上游连接被关闭');

  store.addTurn();
  replay(store,[{type:'agent_start'},{type:'message_started',message_id:'msg_retry'}]);
  assert.equal(turns(store)[0].running,false,'the earlier failure stays completed');
  assert.equal(turns(store)[1].running,true,'an actual retry waiting for tokens stays running');
  assert.equal(turns(store)[1].error,undefined);
});

test('a failed or stopped snapshot before the first assistant token retains its terminal row',()=>{
  const store=createTranscriptStore();
  const messages=[{role:'user',content:'用户自评是 HALV 吗？'}];
  store.hydrate({messages,running:false,last_error:'远端关闭连接'});
  assert.equal(turns(store).length,1);
  assert.equal(turns(store)[0].running,false);
  assert.equal(turns(store)[0].error,'远端关闭连接');
  store.hydrate({messages,running:false,last_stopped:true});
  assert.equal(turns(store).length,1);
  assert.equal(turns(store)[0].running,false);
  assert.equal(turns(store)[0].stopped,true);
});

test('stopping closes the pending response and tool state before the saved event',()=>{
  const store=createTranscriptStore();
  store.addUser({text:'绘制功率谱。'});
  replay(store,[
    {type:'message_started',message_id:'msg_stop'},
    {type:'message_delta',message_id:'msg_stop',text:'准备绘图'},
    {type:'tool_execution_start',message_id:'msg_stop',tool_call_id:'call_stop',tool_name:'plot',args:{kind:'psd'}},
    {type:'stopped'},
  ]);
  assert.equal(turns(store)[0].running,false);
  assert.equal(turns(store)[0].stopped,true);
  assert.equal((entry(store,'msg_stop') as Message).done,true);
  assert.equal((entry(store,'call_stop') as Tool).status,'interrupted');
});

test('public progress streams before tool completion, then a separate answer streams and a stopped follow-up retains its text',()=>{
  const store=createTranscriptStore();
  store.addUser({text:'检查采样率，再解释频谱。'});
  replay(store,[
    {type:'agent_start',run_id:'run_first',sequence:1},
    {type:'turn_start',run_id:'run_first',sequence:2},
    {type:'message_started',message_id:'msg_progress'},
    {type:'message_delta',message_id:'msg_progress',text:'先核对'},
    {type:'message_delta',message_id:'msg_progress',text:'采样信息。'},
  ]);
  assert.equal((entry(store,'msg_progress') as Message).text,'先核对采样信息。');
  assert.equal((entry(store,'msg_progress') as Message).done,false);
  const turnId=turns(store)[0].id;
  replay(store,[
    {type:'message_phase',message_id:'msg_progress',phase:'commentary'},
    {type:'tool_call_started',message_id:'msg_progress',tool_call_id:'call_inspect',tool_name:'inspect_record'},
    {type:'message_completed',message_id:'msg_progress',phase:'commentary'},
    {type:'tool_execution_start',tool_call_id:'call_inspect',tool_name:'inspect_record',args:'{"manifest_id":"doc_eeg"}'},
    {type:'tool_execution_end',tool_call_id:'call_inspect',tool_name:'inspect_record',result:{status:'ok',sample_rate_hz:128}},
    {type:'turn_start'},
    {type:'message_started',message_id:'msg_answer'},
    {type:'message_delta',message_id:'msg_answer',text:'登记采样率为 128 Hz；'},
  ]);
  assert.equal((entry(store,'msg_answer') as Message).done,false);
  assert.equal(turns(store)[0].id,turnId);
  replay(store,[
    {type:'message_delta',message_id:'msg_answer',text:'这不证明官方数据来源。'},
    {type:'message_completed',message_id:'msg_answer',phase:'final_answer'},
    {type:'session_saved'},
  ]);
  assert.deepEqual(turns(store)[0].entries.map(item=>item.id),['msg_progress','call_inspect','msg_answer']);
  assert.equal((entry(store,'msg_progress') as Message).phase,'commentary');
  assert.equal((entry(store,'msg_answer') as Message).phase,'answer');
  assert.equal((entry(store,'call_inspect') as Tool).status,'done');

  store.addUser({text:'继续说明限制。'});
  replay(store,[
    {type:'agent_start',run_id:'run_second',sequence:1},
    {type:'message_started',message_id:'msg_followup'},
    {type:'message_delta',message_id:'msg_followup',text:'尚未核验采集来源'},
    {type:'stopped'},
    {type:'session_saved'},
  ]);
  assert.equal(turns(store).length,2);
  assert.equal(turns(store)[0].id,turnId);
  assert.equal((entry(store,'msg_followup') as Message).text,'尚未核验采集来源');
  assert.equal((entry(store,'msg_followup') as Message).phase,'partial');
  assert.equal(turns(store)[1].stopped,true);
});

test('restoring a running snapshot combines checkpoint messages with its live suffix and then accepts only the unread stream tail',()=>{
  const store=createTranscriptStore();
  const snapshot={running:true,messages:[
    {role:'user',content:'分析这段记录。'},
    {role:'assistant',message_id:'msg_saved',phase:'commentary',content:'先读取记录。',tool_calls:[{id:'call_saved',function:{name:'inspect_record',arguments:'{}'}}]},
  ],live:{run_id:'run_resume',sequence:9,events:[
    {type:'tool_execution_start',run_id:'run_resume',sequence:6,tool_call_id:'call_saved',tool_name:'inspect_record',args:{}},
    {type:'tool_execution_end',run_id:'run_resume',sequence:7,tool_call_id:'call_saved',tool_name:'inspect_record',result:{status:'ok'}},
    {type:'message_started',run_id:'run_resume',sequence:8,message_id:'msg_live'},
    {type:'message_delta',run_id:'run_resume',sequence:9,message_id:'msg_live',text:'观测到 alpha'},
  ]}};
  store.hydrate(snapshot);
  replay(store,snapshot.live.events);
  assert.equal((entry(store,'msg_live') as Message).text,'观测到 alpha');
  assert.equal((entry(store,'msg_live') as Message).done,false);
  const checkpointOwner=turns(store)[0].id;
  // This is the /events?run_id=run_resume&after=9 tail: no repeated started or prefix delta.
  replay(store,[
    {type:'message_delta',run_id:'run_resume',sequence:10,message_id:'msg_live',text:' 谱峰。'},
    {type:'message_completed',run_id:'run_resume',sequence:11,message_id:'msg_live',phase:'final_answer'},
    {type:'session_saved',run_id:'run_resume',sequence:12},
  ]);
  assert.equal(turns(store).length,1);
  assert.equal(turns(store)[0].id,checkpointOwner);
  assert.deepEqual(turns(store)[0].entries.map(item=>item.id),['msg_saved','call_saved','msg_live']);
  assert.equal((entry(store,'msg_live') as Message).text,'观测到 alpha 谱峰。');
  assert.equal((entry(store,'msg_live') as Message).phase,'answer');
  assert.equal((entry(store,'call_saved') as Tool).status,'done');
});

test('steering does not move an older message, its late tool call or its artifact into the next user turn',()=>{
  const store=createTranscriptStore();
  store.addUser({text:'绘制功率谱。'});
  replay(store,[
    {type:'message_started',message_id:'msg_old'},
    {type:'message_delta',message_id:'msg_old',text:'准备绘图'},
  ]);
  const oldTurn=turns(store)[0].id;
  store.addUser({text:'再补充数据来源限制。'});
  replay(store,[
    {type:'message_delta',message_id:'msg_old',text:'，保留原始单位。'},
    {type:'message_phase',message_id:'msg_old',phase:'commentary'},
    {type:'tool_call_started',message_id:'msg_old',tool_call_id:'call_old_plot',tool_name:'plot'},
    {type:'message_completed',message_id:'msg_old',phase:'commentary'},
    {type:'tool_execution_start',tool_call_id:'call_old_plot',tool_name:'plot',args:{kind:'psd'}},
    {type:'tool_execution_end',tool_call_id:'call_old_plot',tool_name:'plot',result:{status:'ok'}},
    {type:'artifact_ready',tool_call_id:'call_old_plot',tool_name:'plot',result:{artifact_id:'art_abcdef',kind:'plot'}},
  ]);
  assert.equal(turns(store).length,1,'old events must not insert an empty new agent turn');
  assert.equal(turns(store)[0].id,oldTurn);
  assert.deepEqual(turns(store)[0].entries.map(item=>item.id),['msg_old','call_old_plot']);
  assert.deepEqual(turns(store)[0].artifacts,['art_abcdef']);
  replay(store,[
    {type:'turn_start'},
    {type:'message_started',message_id:'msg_new'},
    {type:'message_delta',message_id:'msg_new',text:'图已生成；来源仍未核验。'},
    {type:'message_completed',message_id:'msg_new',phase:'final_answer'},
    {type:'session_saved'},
  ]);
  assert.equal(turns(store).length,2);
  assert.deepEqual(turns(store)[1].entries.map(item=>item.id),['msg_new']);
  assert.deepEqual(turns(store)[1].artifacts,[]);
  assert.equal((entry(store,'msg_old') as Message).text,'准备绘图，保留原始单位。');
});

test('an explicit process fold survives new deltas and completion, and answering a question clears the old waiting state',()=>{
  const store=createTranscriptStore();
  store.addUser({text:'分析睡眠状态。'});
  replay(store,[
    {type:'message_started',message_id:'msg_question'},
    {type:'message_delta',message_id:'msg_question',text:'先确认采样率。'},
    {type:'message_phase',message_id:'msg_question',phase:'commentary'},
  ]);
  const firstTurn=turns(store)[0].id;
  store.toggle(firstTurn);
  store.toggle(firstTurn);
  replay(store,[
    {type:'message_delta',message_id:'msg_question',text:'请提供设备登记值。'},
    {type:'message_completed',message_id:'msg_question',phase:'commentary'},
    {type:'user_input_requested',result:{request:{id:'ask_rate'}}},
    {type:'session_saved'},
  ]);
  assert.equal(turns(store)[0].open,false);
  assert.equal(turns(store)[0].waiting,true);
  store.metadata();
  assert.equal(turns(store)[0].open,false);
  store.toggle(firstTurn);
  store.addUser({text:'128 Hz',questions:['采样率是多少？']});
  replay(store,[
    {type:'message_started',message_id:'msg_after_question'},
    {type:'message_delta',message_id:'msg_after_question',text:'已登记为用户提供的信息。'},
    {type:'message_completed',message_id:'msg_after_question',phase:'final_answer'},
    {type:'session_saved'},
  ]);
  assert.equal(turns(store)[0].open,true,'a user-expanded completed process must stay expanded');
  assert.equal(turns(store)[0].waiting,false,'answered questions must not leave an obsolete waiting label');
  assert.equal(turns(store)[0].id,firstTurn);
});

test('thinking activity is transient and tool completion does not finish the entire request',()=>{
 const store=createTranscriptStore();store.addTurn('thinking');
 store.apply({type:'message_started',message_id:'model'});
 store.apply({type:'model_activity',message_id:'model',phase:'thinking'});
 assert.equal(turns(store)[0].activity,'thinking');
 assert.equal(typeof turns(store)[0].startedAt,'number');
 store.tool('measure','analyze_signal',{},'running');store.tool('measure','analyze_signal',{},'done',{});
 assert.equal(turns(store)[0].running,true);assert.equal(turns(store)[0].finishedAt,undefined);
 store.finish();assert.equal(turns(store)[0].activity,undefined);assert.equal(typeof turns(store)[0].finishedAt,'number');
 store.hydrate({messages:[{role:'assistant',content:'结果。'}],running:false});
 assert.equal(turns(store)[0].activity,undefined);assert.equal(turns(store)[0].startedAt,undefined);
 store.hydrate({messages:[],running:true,live:{started_at:'2026-10-20T07:00:00Z'}});
 assert.equal(turns(store)[0].startedAt,Date.parse('2026-10-20T07:00:00Z'));
 store.finish();
});

test('artifact events and immutable citation text remain available across final classification and metadata updates',()=>{
  const store=createTranscriptStore();
  store.addUser({text:'给出有出处的频谱解释。'});
  replay(store,[
    {type:'message_started',message_id:'msg_plot_progress'},
    {type:'message_phase',message_id:'msg_plot_progress',phase:'commentary'},
    {type:'tool_call_started',message_id:'msg_plot_progress',tool_call_id:'call_plot',tool_name:'plot'},
    {type:'message_completed',message_id:'msg_plot_progress',phase:'commentary'},
    {type:'tool_execution_start',tool_call_id:'call_plot',tool_name:'plot',args:{kind:'psd'}},
    {type:'artifact_ready',tool_call_id:'call_plot',result:{artifact_id:'art_abcdef',kind:'plot'}},
    {type:'artifact_ready',tool_call_id:'call_plot',result:{artifact_id:'art_abcdef',kind:'plot'}},
    {type:'tool_execution_end',tool_call_id:'call_plot',tool_name:'plot',result:{status:'ok'}},
    {type:'message_started',message_id:'msg_cited'},
    {type:'message_delta',message_id:'msg_cited',text:'![功率谱](artifact://art_abcdef)\n\n依据 [k_123456]，'},
  ]);
  const beforeMetadata=store.getSnapshot();
  store.metadata();
  assert.notEqual(store.getSnapshot(),beforeMetadata,'source metadata must notify the React subscriber');
  assert.equal(store.getSnapshot().rows,beforeMetadata.rows,'metadata refresh must preserve transcript objects and text');
  replay(store,[
    {type:'message_delta',message_id:'msg_cited',text:'该观察不等于诊断。代码中的 `k_deadbeef` 是字面内容。'},
    {type:'message_completed',message_id:'msg_cited',phase:'final_answer',citation_ids:['k_123456']},
    {type:'session_saved'},
  ]);
  assert.deepEqual(turns(store)[0].artifacts,['art_abcdef']);
  assert.equal((entry(store,'msg_cited') as Message).text,'![功率谱](artifact://art_abcdef)\n\n依据 [k_123456]，该观察不等于诊断。代码中的 `k_deadbeef` 是字面内容。');
  assert.equal((entry(store,'msg_cited') as Message).phase,'answer');
});


test('public execution stages stay with their tool across steering and restore from saved results',()=>{
 const store=createTranscriptStore();store.addTurn('first');store.tool('remote','run_model',{},'running');
 store.apply({type:'tool_execution_progress',tool_call_id:'remote',result:{stage:'inference',label:'推理中',detail:'EEG'}});
 store.addUser({text:'只解释 EEG 的结果。'});store.addTurn('second');
 store.apply({type:'tool_execution_progress',tool_call_id:'remote',result:{stage:'download',label:'取回结果',detail:''}});
 assert.deepEqual((entry(store,'remote') as any).progress.map((value:any)=>value.stage),['inference','download']);
 assert.equal(turns(store)[1].entries.length,0);
 store.hydrate({messages:[{role:'assistant',content:'',tool_calls:[{id:'remote',function:{name:'run_model',arguments:'{}'}}]},{role:'tool',tool_call_id:'remote',content:JSON.stringify({status:'ok',execution_progress:[{stage:'inference',label:'推理中',detail:'EEG'}]})}]});
 assert.equal((entry(store,'remote') as any).progress[0].label,'推理中');
});
