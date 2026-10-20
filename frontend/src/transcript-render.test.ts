import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRequire} from 'node:module';
import {AgentTurn,type Options} from './transcript';
import type {Turn} from './transcript-store';

const require=createRequire(import.meta.url);
(globalThis as any).IconSystem=require('../../gateway/internal/server/static/icons.js');
const artifact={artifact_id:'art_abcdef',kind:'plot',summary:{kind:'psd'}};
const source={chunk_id:'k_abcdef',title:'EEG指南.pdf',text:'原始引用',page:2};
const options:Options={session:()=> 'session',source:id=>id===source.chunk_id?source:null,artifact:id=>id===artifact.artifact_id?artifact:null,sourceTitle:value=>value.title,sourceLocation:value=>'第 '+value.page+' 页',references:text=>[...text.matchAll(/k_[0-9a-f]{6,}/g)].map(match=>match[0]),artifactTitle:()=> '功率谱',toolLabel:name=>name,formatArgs:()=> '',toolSummary:()=> '',toolVisible:()=>true,openResults(){},openArtifact(){},openAttachment(){},openReference(){},openTrace(){},copy(){},retry(){},changed(){}};
const render=(row:Turn)=>renderToStaticMarkup(React.createElement(AgentTurn,{row,options,toggle(){},engage(){},metadataVersion:0}));

test('rejected argument generation is shown as unexecuted with a readable explanation',()=>{
  const row:Turn={type:'turn',id:'rejected-turn',running:false,open:true,waiting:false,stopped:false,artifacts:[],entries:[{type:'tool',id:'bad',name:'run_python',args:{},status:'error',result:{status:'error',error:{code:'bad_args',message:'arguments are not valid JSON'}}}]};
  const html=render(row);
  assert.match(html,/未执行/);assert.match(html,/调用参数格式有误/);assert.doesNotMatch(html,/arguments are not valid JSON/);
});

test('local script stages show actual file progress with no remote upload label',()=>{
  const row:Turn={type:'turn',id:'script-turn',running:true,waiting:false,stopped:false,artifacts:[],entries:[{type:'tool',id:'script',name:'run_python',args:{label:'清洗并提取特征'},status:'running',progress:[{stage:'script_call_1',label:'检查数据结构',detail:'subject-a.npz'},{stage:'script_call_2',label:'预处理',detail:'subject-a.npz'}]}]};
  const html=render(row);
  assert.match(html,/预处理/);
  assert.match(html,/subject-a.npz/);
  assert.doesNotMatch(html,/active-status/);
  assert.doesNotMatch(html,/检查数据结构/,'a collapsed batch shows the current stage rather than all earlier files');
  assert.doesNotMatch(html,/远程执行阶段|上传 EEG|远程推理/);
});

test('download references use actual artifact identities and a fabricated suffix is not a download',()=>{
  const row:Turn={type:'turn',id:'turn',running:false,waiting:false,stopped:false,artifacts:[],entries:[{type:'message',id:'msg',phase:'answer',done:true,text:'[结果](artifact://art_abcdef) [错误文件名](artifact://art_abcdef.csv)'}]};
  const html=render(row);
  assert.match(html,/href="\/artifacts\/art_abcdef\/file"/);
  assert.equal((html.match(/data-art="art_abcdef"/g)||[]).length,1);
  assert.doesNotMatch(html,/href="[^"]*\.csv/);
});

test('a figure streamed in pending text is already visible once, and stays once after final classification',()=>{
  const row:Turn={type:'turn',id:'turn',running:true,waiting:false,stopped:false,artifacts:['art_abcdef'],entries:[{type:'message',id:'msg',phase:'pending',done:false,text:'![功率谱](artifact://art_abcdef)'}]};
  const streaming=render(row);
  assert.equal((streaming.match(/<img /g)||[]).length,1);
  assert.match(streaming,/data-phase="pending"/);
  const complete=render({...row,running:false,entries:[{...row.entries[0],phase:'answer',done:true} as any]});
  assert.equal((complete.match(/<img /g)||[]).length,1);
  const textOnlyLink=render({...row,entries:[{type:'message',id:'msg',phase:'pending',done:false,text:'[查看功率谱](artifact://art_abcdef)'}]});
  assert.equal((textOnlyLink.match(/<img /g)||[]).length,0,'a text link opens the selected result without duplicating a gallery');
  assert.match(textOnlyLink,/查看本轮全部结果：1 张图/);
});
test('citation links render a real numbered source, unknown references stay unresolved and code remains literal',()=>{
  const row:Turn={type:'turn',id:'turn',running:false,waiting:false,stopped:false,artifacts:[],entries:[{type:'message',id:'msg',phase:'answer',done:true,text:'真实来源 [k_abcdef] 和 [指南](k_abcdef)。未知 [k_123456]。\n\n`k_abcdef`'}]};
  const html=render(row);
  assert.equal((html.match(/data-know="k_abcdef"/g)||[]).length,3,'two inline refs and one source chip');
  assert.match(html,/来源 1：EEG指南.pdf · 第 2 页/);
  assert.match(html,/citation-ref unavailable/);
  assert.match(html,/<code>k_abcdef<\/code>/);
  assert.doesNotMatch(html,/citation:\[/);
});

test('an interrupted tool in a failed or stopped request never presents an active progress label',()=>{
  const row:Turn={type:'turn',id:'turn',running:false,waiting:false,stopped:false,error:'上游连接被关闭',artifacts:[],entries:[{type:'tool',id:'call',name:'plot',args:{kind:'psd'},status:'interrupted'}]};
  const failed=render(row);
  assert.match(failed,/请求未完成/);
  assert.match(failed,/data-status="interrupted"/);
  assert.doesNotMatch(failed,/正在生成|正在plot|working-dot/);
  const stopped=render({...row,error:undefined,stopped:true});
  assert.match(stopped,/已停止/);
  assert.doesNotMatch(stopped,/已完成分析|正在plot|working-dot/);
});

test('a connection failure has a readable summary and retains the exact network error in closed details',()=>{
  const error='read tcp 10.121.16.96:63171->104.21.18.112:443: wsarecv: An existing connection was forcibly closed by the remote host.';
  const row:Turn={type:'turn',id:'turn',running:false,waiting:false,stopped:false,error,artifacts:[],entries:[]};
  const html=render(row);
  assert.match(html,/<p>模型服务连接中断，请重试。<\/p>/);
  assert.match(html,/<details class="error-details"><summary>查看错误详情<\/summary><p>read tcp/);
  assert.match(html,/forcibly closed by the remote host/);
  assert.doesNotMatch(html,/<details[^>]* open/);
});


test('adjacent public tools form compact groups separated by narrative, with real result summaries',()=>{
  const row:Turn={type:'turn',id:'grouped',running:false,open:true,waiting:false,stopped:false,artifacts:[],entries:[
    {type:'message',id:'intro',phase:'commentary',done:true,text:'先核对数据。'},
    {type:'tool',id:'read',name:'read_document',args:{},status:'done',result:{}},
    {type:'tool',id:'internal',name:'read_skill',args:{},status:'done',result:{}},
    {type:'tool',id:'inspect',name:'inspect_record',args:{},status:'done',result:{}},
    {type:'message',id:'progress',phase:'commentary',done:true,text:'开始计算。'},
    {type:'tool',id:'plot',name:'plot',args:{},status:'done',result:{}},
    {type:'message',id:'final',phase:'answer',done:true,text:'结果已保存。'},
  ]};
  const html=renderToStaticMarkup(React.createElement(AgentTurn,{row,options:{...options,toolVisible:name=>name!=='read_skill',toolSummary:()=> '已核对 2 通道 · 128 Hz'},toggle(){},engage(){},metadataVersion:0}));
  assert.equal((html.match(/data-group-id=/g)||[]).length,2);
  assert.doesNotMatch(html,/data-tool-id="internal"/);
  assert.match(html,/id="tools-read" class="operation-details" hidden/);
  assert.match(html,/已核对 2 通道 · 128 Hz/);
  assert.ok(html.indexOf('data-message-id="intro"')<html.indexOf('data-group-id="read"'));
  assert.ok(html.indexOf('data-group-id="read"')<html.indexOf('data-message-id="progress"'));
  assert.ok(html.indexOf('data-group-id="plot"')<html.indexOf('data-message-id="final"'));
});

test('a recovered tool failure remains inspectable without declaring a completed answer failed',()=>{
  const row:Turn={type:'turn',id:'recovered',running:false,waiting:false,stopped:false,artifacts:[],entries:[
    {type:'tool',id:'failed',name:'plot',args:{},status:'error',result:{error:{message:'缺少输入'}}},
    {type:'tool',id:'retry',name:'plot',args:{},status:'done',result:{}},
    {type:'message',id:'final',phase:'answer',done:true,text:'已补充输入并生成图表。'},
  ]};
  const html=render(row);
  assert.doesNotMatch(html,/active-status|项操作|正在整理结果/);
  assert.match(html,/已补充输入并生成图表。/);
  assert.match(html,/data-tool-id="failed" data-status="error"/);
  assert.match(html,/缺少输入/);
  assert.doesNotMatch(html,/role="alert"/);
});

test('waiting for input keeps its context open and does not animate model activity',()=>{
  const html=render({type:'turn',id:'waiting',running:false,waiting:true,stopped:false,artifacts:[],entries:[{type:'message',id:'question',phase:'commentary',done:true,text:'请确认采样率。'}]});
  assert.match(html,/等待你的回答/);
  assert.match(html,/transcript-entries process-open/);
  assert.doesNotMatch(html,/activity-summary running/);
});


test('registered download paths preserve link labels, literal code and incremental incomplete links',()=>{
 const row:Turn={type:'turn',id:'links',running:false,waiting:false,stopped:false,artifacts:[],entries:[{type:'message',id:'reply',phase:'answer',done:true,text:'产物：[审阅记录](/artifacts/art_abcdef/file)。`/artifacts/art_abcdef/file`'}]};
 const html=render(row);
 assert.match(html,/<a class="art" href="\/artifacts\/art_abcdef\/file" data-art="art_abcdef">审阅记录<\/a>/);
 assert.match(html,/<code>\/artifacts\/art_abcdef\/file<\/code>/);
 assert.doesNotMatch(html,/\[审阅记录\]|\/artifacts\/功率谱/);
 const partial=render({...row,running:true,entries:[{type:'message',id:'reply',phase:'pending',done:false,text:'[审阅记录](/artifacts/art_abcdef/fi'}]});
 assert.doesNotMatch(partial,/artifact:\/\/art_abcdef/);
 assert.match(partial,/art_abcdef\/fi/);
});

test('current activity follows streamed narrative and tools at the bottom, with remote stages from real events',()=>{
 const row:Turn={type:'turn',id:'running',running:true,open:false,waiting:false,stopped:false,artifacts:[],entries:[
 {type:'message',id:'comment',phase:'commentary',done:true,text:'信号已准备好，正在送往服务器。'},
 {type:'tool',id:'remote',name:'run_model',args:{},status:'running',progress:[{stage:'upload_eeg',label:'上传 EEG 输入',detail:'256 KB'},{stage:'inference',label:'远程推理中',detail:'EEG'}]},
 ]};
 const html=render(row);
 assert.ok(html.indexOf('data-message-id="comment"')<html.indexOf('data-group-id="remote"'));
 assert.ok(html.indexOf('data-group-id="remote"')<html.indexOf('class="active-status"'));
 assert.doesNotMatch(html,/上传 EEG 输入/);
 assert.match(html,/远程推理中/);
 assert.match(html,/处理过程/);
 assert.doesNotMatch(html,/项操作|正在整理结果/);
 const local=render({...row,entries:[row.entries[0],{type:'tool',id:'local',name:'inspect_record',args:{},status:'running'}]});
 assert.doesNotMatch(local,/远程执行阶段|上传 EEG 输入/);
});

test('a waiting model has a compact thinking state and finished results keep the answer outside the process',()=>{
 const base:Turn={type:'turn',id:'thinking',running:true,waiting:false,stopped:false,activity:'thinking',artifacts:[],entries:[]};
 assert.match(render(base),/data-activity="thinking"/);assert.match(render(base),/思考中/);
 const done=render({...base,running:false,entries:[{type:'message',id:'plan',phase:'commentary',done:true,text:'先读取文件。'},{type:'message',id:'answer',phase:'answer',done:true,text:'主峰为 10 Hz。'}]});
 assert.match(done,/已完成/);assert.match(done,/主峰为 10 Hz/);assert.doesNotMatch(done,/active-status/);
 assert.match(done,/aria-expanded="false"/);
});

test('one active tool stage owns the current progress instead of repeating it as model activity',()=>{
 const row:Turn={type:'turn',id:'one-progress',running:true,waiting:false,stopped:false,artifacts:[],entries:[{type:'tool',id:'features',name:'analyze_signal',args:{},status:'running',progress:[{stage:'features',label:'计算逐窗特征',detail:'synthetic-8ch-60s.npz'}]}]};
 const html=render(row);
 assert.equal((html.match(/计算逐窗特征/g)||[]).length,1);
 assert.match(html,/synthetic-8ch-60s.npz/);
 assert.doesNotMatch(html,/class="active-status"/);
 assert.match(render({...row,open:false}),/class="active-status"/,'folding the visible tool still leaves one current status');
});

test('serializing the same feature table to CSV twice produces one download while JSON stays separate',()=>{
 const records:any={
  features:{artifact_id:'features',kind:'features'},
  csv:{artifact_id:'csv',kind:'table',summary:{source:'features',format:'csv'},provenance:{created_by:'export@1'}},
  csvAgain:{artifact_id:'csvAgain',kind:'table',summary:{source:'csv',format:'csv'},provenance:{created_by:'export@1'}},
  json:{artifact_id:'json',kind:'table',summary:{source:'csv',format:'json'},provenance:{created_by:'export@1'}},
 };
 const row:Turn={type:'turn',id:'exports',running:false,waiting:false,stopped:false,entries:[],artifacts:['csv','csvAgain','json']};
 const html=renderToStaticMarkup(React.createElement(AgentTurn,{row,options:{...options,artifact:id=>records[id],artifactTitle:()=> '逐窗特征'},toggle(){},engage(){},metadataVersion:0}));
 assert.equal((html.match(/class="turn-results-link"/g)||[]).length,1);
 assert.match(html,/2 份表/);assert.doesNotMatch(html,/turn-gallery/);
});
