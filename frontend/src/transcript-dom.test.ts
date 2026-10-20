import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {JSDOM} from 'jsdom';
import {flushSync} from 'react-dom';
import {act} from 'react';
import {mount,type Options} from './transcript';

test('workspace outputs survive history replay and open one scoped catalog instead of another partial file grid',()=>{
  const dom=new JSDOM('<div id="chat"></div>',{url:'http://localhost/'});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement});
  (globalThis as any).IconSystem=createRequire(import.meta.url)('../../gateway/internal/server/static/icons.js');
  const opened:unknown[]=[];
  const options:Options={session:()=> 'session',source:()=>null,artifact:()=>null,sourceTitle:()=>'',sourceLocation:()=>'',references:()=>[],artifactTitle:()=>'',toolLabel:name=>name,formatArgs:()=>'',toolSummary:()=>'',toolVisible:()=>true,openResults:id=>opened.push(id),openArtifact(){},openAttachment(){},openReference:file=>opened.push(file),openTrace(){},copy(){},retry(){},changed(){}};
  const script={workspace_id:'ws_project',path:'results/分析.py',name:'分析.py',kind:'file'};
  const table={workspace_id:'ws_project',path:'results/features.csv',name:'features.csv',kind:'file'};
  let store:ReturnType<typeof mount>;
  flushSync(()=>{store=mount(document.querySelector('#chat')!,options)});
  flushSync(()=>store.hydrate({messages:[
    {role:'user',content:'处理数据并保存结果'},
    {role:'assistant',tool_calls:[{id:'write',function:{name:'write_file',arguments:'{}'}},{id:'edit',function:{name:'edit_file',arguments:'{}'}},{id:'run',function:{name:'run_python',arguments:'{}'}}]},
    {role:'tool',tool_call_id:'write',content:JSON.stringify({status:'ok',workspace_file:script})},
    {role:'tool',tool_call_id:'edit',content:JSON.stringify({status:'ok',workspace_file:script})},
    {role:'tool',tool_call_id:'run',content:JSON.stringify({status:'error',error:{message:'后续图表未完成'},workspace_files:[table]})},
    {role:'assistant',content:'脚本与特征表已保存。'}
  ]}));
  assert.equal(document.querySelectorAll('[data-workspace-file]').length,0);
  document.querySelector<HTMLButtonElement>('.turn-results-link')!.click();
  assert.deepEqual(opened,['turn-history-1']);
  assert.match(document.querySelector('.turn-results-link')!.textContent!,/2 个工作区文件/);
  dom.window.close();
});

test('streaming retains the existing paragraph, source button, image and user text selection across deltas and final classification',()=>{
  const dom=new JSDOM('<div id="chat"></div>',{url:'http://localhost/'});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement});
  (globalThis as any).IconSystem=createRequire(import.meta.url)('../../gateway/internal/server/static/icons.js');
  const source={title:'研究方法',page:1},artifact={artifact_id:'art_abcdef',kind:'plot'};
  const opened:string[]=[];
  const options:Options={session:()=> 'session',source:id=>id==='k_abcdef'?source:null,artifact:id=>id==='art_abcdef'?artifact:null,sourceTitle:value=>value.title,sourceLocation:()=> '第1页',references:text=>text.includes('k_abcdef')?['k_abcdef']:[],artifactTitle:()=> '功率谱',toolLabel:name=>name,formatArgs:()=> '',toolSummary:()=> '',toolVisible:()=>true,openResults(){},openArtifact(){},openAttachment:id=>opened.push(id),openReference(){},openTrace(){},copy(){},retry(){},changed(){}};
  let store:ReturnType<typeof mount>;
  flushSync(()=>{store=mount(document.querySelector('#chat')!,options)});
  flushSync(()=>{store.addUser({text:'请分析。',attachments:[{id:'vid_original',name:'participant.mp4',modality:'video'}]});store.addTurn();store.artifact('art_abcdef');store.apply({type:'message_started',message_id:'msg_live'});store.apply({type:'message_delta',message_id:'msg_live',text:'已完成的第一段。\n\n有依据 [k_abcdef]。\n\n![功率谱](artifact://art_abcdef)\n\n继续输出'})});
  document.querySelector<HTMLButtonElement>('.message-file')!.click();assert.deepEqual(opened,['vid_original']);
  const paragraph=document.querySelector('[data-message-id] p')!,citation=document.querySelector('.citation-ref')!,image=document.querySelector('img.report-image')!;
  citation.setAttribute('aria-expanded','true');
  const range=document.createRange();range.setStart(paragraph.firstChild!,0);range.setEnd(paragraph.firstChild!,3);
  const selection=document.getSelection()!;selection.removeAllRanges();selection.addRange(range);
  const selected=selection.toString();
  for(const text of ['第一批 token','，第二批 token','。\n\n新段落'])flushSync(()=>store.apply({type:'message_delta',message_id:'msg_live',text}));
  flushSync(()=>{store.metadata();store.apply({type:'message_completed',message_id:'msg_live',phase:'final_answer'});store.apply({type:'session_saved'})});
  assert.equal(document.querySelector('[data-message-id] p'),paragraph);
  assert.equal(document.querySelector('.citation-ref'),citation);
  assert.equal(document.querySelector('img.report-image'),image);
  assert.equal(document.querySelectorAll('img').length,1);
  assert.equal(citation.getAttribute('aria-expanded'),'true');
  assert.equal(selection.toString(),selected);
  assert.equal(document.querySelector('[data-message-id]')!.getAttribute('data-phase'),'answer');
  dom.window.close();
});


test('tool disclosure, narrative DOM and scoped detail actions survive streaming without requesting a bottom scroll',()=>{
  const dom=new JSDOM('<div id="chat"></div>',{url:'http://localhost/'});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement});
  (globalThis as any).IconSystem=createRequire(import.meta.url)('../../gateway/internal/server/static/icons.js');
  let changes=0;
  const details:(string|undefined)[]=[];
  const options:Options={session:()=> 'session',source:()=>null,artifact:()=>null,sourceTitle:()=>'',sourceLocation:()=>'',references:()=>[],artifactTitle:()=>'',toolLabel:name=>name,formatArgs:()=> '输入参数',toolSummary:()=> '已生成图表',toolVisible:()=>true,openResults(){},openArtifact(){},openAttachment(){},openReference(){},openTrace:id=>details.push(id),copy(){},retry(){},changed(){changes++}};
  let store:ReturnType<typeof mount>;
  flushSync(()=>{store=mount(document.querySelector('#chat')!,options)});
  flushSync(()=>{
    store.addTurn('turn');
    store.message('intro','正在检查数据。','pending',false);
  });
  const paragraph=document.querySelector('[data-message-id="intro"] p');
  flushSync(()=>{
    store.apply({type:'message_completed',message_id:'intro',phase:'commentary'});
    store.tool('plot','plot',{},'running');
  });
  assert.equal(document.querySelector('[data-message-id="intro"] p'),paragraph,'classification must retain the narrative DOM');
  const button=document.querySelector<HTMLButtonElement>('.operation-summary')!;
  const before=changes;
  flushSync(()=>button.click());
  assert.equal(changes,before,'disclosure is not new content and must not request an auto-scroll');
  assert.equal(button.getAttribute('aria-expanded'),'true');
  const detailButton=document.querySelector<HTMLButtonElement>('.step-detail')!;
  assert.equal(detailButton.title,'查看执行详情');
  detailButton.click();
  assert.deepEqual(details,['plot']);
  flushSync(()=>{
    store.tool('plot','plot',{},'done',{});
    store.message('answer','图表已生成。','pending',false);
    store.message('answer','图表已生成。可以预览。','answer',true);
    store.finish();
  });
  assert.equal(document.querySelector('.operation-summary'),button);
  assert.equal(button.getAttribute('aria-expanded'),'true');
  assert.equal(document.querySelector('.operation-details')!.hasAttribute('hidden'),false);
  assert.equal(document.querySelector('.activity-summary')!.getAttribute('aria-expanded'),'true','engaging with steps pins the process open');
  assert.equal(document.querySelectorAll('.turn-process button').length,1,'completed replies expose one process disclosure rather than a second technical-record action');
  const after=changes;
  flushSync(()=>document.querySelector<HTMLButtonElement>('.activity-summary')!.click());
  assert.equal(changes,after);
  assert.equal(document.querySelector('.activity-summary')!.getAttribute('aria-expanded'),'false');
  dom.window.close();
});

test('progress previews expire, completed work folds, and manually opened details remain available',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const dom=new JSDOM('<div id="chat"></div>',{url:'http://localhost/'});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement,IS_REACT_ACT_ENVIRONMENT:true});
  (globalThis as any).IconSystem=createRequire(import.meta.url)('../../gateway/internal/server/static/icons.js');
  const options:Options={session:()=> 'session',source:()=>null,artifact:()=>null,sourceTitle:()=>'',sourceLocation:()=>'',references:()=>[],artifactTitle:()=>'',toolLabel:name=>name,formatArgs:()=>'',toolSummary:()=> '已完成',toolVisible:()=>true,openResults(){},openArtifact(){},openAttachment(){},openReference(){},openTrace(){},copy(){},retry(){},changed(){}};
  let store:ReturnType<typeof mount>;
  await act(async()=>{store=mount(document.querySelector('#chat')!,options)});
  t.after(async()=>{await act(async()=>store.finish());dom.window.close();(globalThis as any).IS_REACT_ACT_ENVIRONMENT=false});
  await act(async()=>{store.addTurn('progress');store.message('intro','正在核对采样率。','commentary',true)});
  assert.equal(document.querySelectorAll('.commentary-entry.is-current').length,1);
  await act(async()=>store.tool('prepare','inspect_record',{},'running'));
  assert.equal(document.querySelectorAll('.commentary-entry.is-current').length,1,'public analysis summary remains visible while a tool runs');
  await act(async()=>t.mock.timers.tick(18100));
  assert.equal(document.querySelectorAll('.commentary-entry.is-current').length,0);
  await act(async()=>{store.tool('prepare','inspect_record',{},'done',{});store.tool('measure','analyze_signal',{},'running')});
  assert.equal(document.querySelectorAll('.tool-entry.is-current').length,1);
  assert.equal(document.querySelectorAll('.active-status').length,0,'a visible tool is the only current progress');
  await act(async()=>{store.tool('measure','analyze_signal',{},'done',{});store.message('answer','主峰为 10 Hz。','answer',true);store.finish()});
  assert.equal(document.querySelectorAll('.tool-entry.is-current').length,1);
  await act(async()=>t.mock.timers.tick(5100));
  assert.equal(document.querySelectorAll('.tool-entry.is-current').length,0);
  assert.match(document.querySelector('.answer-entry')!.textContent!,/10 Hz/);
  await act(async()=>document.querySelector<HTMLButtonElement>('.activity-summary')!.click());
  assert.equal(document.querySelector('.transcript-entries')!.classList.contains('process-open'),true);
  assert.match(document.querySelector('.commentary-entry')!.textContent!,/采样率/);
});

test('expanding one running tool shows its name and current stage once with one progress indicator',async t=>{
  const dom=new JSDOM('<div id="chat"></div>',{url:'http://localhost/'});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement,IS_REACT_ACT_ENVIRONMENT:true});
  (globalThis as any).IconSystem=createRequire(import.meta.url)('../../gateway/internal/server/static/icons.js');
  const options:Options={session:()=> 'session',source:()=>null,artifact:()=>null,sourceTitle:()=>'',sourceLocation:()=>'',references:()=>[],artifactTitle:()=>'',toolLabel:()=> '本地信号分析',formatArgs:()=> '质量、频谱与特征',toolSummary:()=>'',toolVisible:()=>true,openResults(){},openArtifact(){},openAttachment(){},openReference(){},openTrace(){},copy(){},retry(){},changed(){}};
  let store:ReturnType<typeof mount>;
  await act(async()=>{store=mount(document.querySelector('#chat')!,options)});
  t.after(async()=>{await act(async()=>store.finish());dom.window.close();(globalThis as any).IS_REACT_ACT_ENVIRONMENT=false});
  await act(async()=>{store.addTurn('one-tool');store.tool('features','analyze_signal',{},'running');store.apply({type:'tool_execution_progress',tool_call_id:'features',result:{stage:'features',label:'计算逐窗特征',detail:'synthetic-8ch-60s.npz'}})});
  assert.equal(document.querySelectorAll('.active-status').length,0);
  await act(async()=>document.querySelector<HTMLButtonElement>('.operation-summary')!.click());
  const text=document.querySelector('.operation-group')!.textContent!;
  assert.equal((text.match(/本地信号分析/g)||[]).length,1);
  assert.equal((text.match(/计算逐窗特征/g)||[]).length,1);
  assert.equal(document.querySelectorAll('.operation-group.active,.step.run,.remote-stage.active').length,1);
});
