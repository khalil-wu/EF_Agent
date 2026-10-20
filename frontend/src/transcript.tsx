import React,{createContext,memo,useContext,useEffect,useLayoutEffect,useMemo,useRef,useState,useSyncExternalStore} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown,{type Components} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {resultItems,resultSummary,resultDescription,workspaceDeliveries,workspaceSummary} from './result-catalog';
export {resultItems,resultSummary,resultTitle,resultDescription,resultFormat,isProcessResult,workspaceDeliveries,workspaceSummary,resultGroupTitle} from './result-catalog';
import {createTranscriptStore,REASONING_PREVIEW_MS,type Message,type Tool,type Turn,type User,type FileReference} from './transcript-store';

declare const IconSystem:{svg(name:string):string};
export type Options={
  session:()=>string;source:(id:string)=>any;artifact:(id:string)=>any;
  sourceTitle:(source:any)=>string;sourceLocation:(source:any)=>string;references:(text:string)=>string[];
  artifactTitle:(artifact:any)=>string;toolLabel:(name:string)=>string;formatArgs:(name:string,args:any)=>string;
  toolSummary:(name:string,result:any)=>string;toolVisible:(name:string)=>boolean;
  openResults:(turnId:string)=>void;openArtifact:(id:string)=>void;openAttachment:(id:string)=>void;openReference:(reference:FileReference)=>void;openTrace:(toolId?:string)=>void;copy:(text:string)=>void;retry:()=>void;changed:()=>void;
};
const Icon=memo(({name}:{name:string})=><span className="ms" aria-hidden="true" dangerouslySetInnerHTML={{__html:IconSystem.svg(name)}}/>);

const duration=(seconds:number)=>seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
function Elapsed({start,end}:{start?:number;end?:number}){
  const [now,setNow]=useState(Date.now);
  useEffect(()=>{if(!start||end)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[start,end]);
  return start?<span className="activity-time">{duration(Math.floor(((end||now)-start)/1000))}</span>:null;
}

function prepareMarkdown(text:string,numbers:Map<string,number>,options:Options){
  return text.split(/(```[\s\S]*?(?:```|$)|`[^`]*`|!?\[[^\]]*\]\([^\s)]+\)|!?\[[^\]]*\]\([^)]*$)/g).map((part,index)=>{
    if(index%2){
      if(part.startsWith('`'))return part;
      const link=part.match(/^(!?\[)([^\]]*)\]\(([^)]+)\)$/);
      if(!link)return part;
      const citation=link[3].match(/^(k_[0-9a-f]{6,})$/);
      if(citation)return `[${numbers.get(citation[1])||'?'}](citation:${citation[1]})`;
      const id=link[3].match(/^artifact:\/\/(art_[0-9a-f]{6,})$/)?.[1]||link[3].match(/^\/artifacts\/(art_[0-9a-f]{6,})\/file(?:\?[^)]+)?$/)?.[1];
      if(id&&options.artifact(id))return `${link[1]}${link[2]===id?options.artifactTitle(options.artifact(id)):link[2]}](artifact://${id})`;
      return part;
    }
    return part
    .replace(/\[([^\]]+)\]\((k_[0-9a-f]{6,})\)|\[(k_[0-9a-f]{6,})\]|\b(k_[0-9a-f]{6,})\b/g,(_,label,link,bracket,bare)=>{const id=link||bracket||bare;return `[${numbers.get(id)||'?'}](citation:${id})`})
    .replace(/\/artifacts\/(art_[0-9a-f]{6,})\/file(?:\?[^\s)]+)?|(?:artifact:\/\/)?\b(art_[0-9a-f]{6,})\b/g,(match,pathId,bareId)=>{const id=pathId||bareId,artifact=options.artifact(id);return artifact?`[${options.artifactTitle(artifact)}](artifact://${artifact.artifact_id})`:match});
  }
  ).join('');
}
const MarkdownContext=createContext<{options:Options;numbers:Map<string,number>}>(null!);
const markdownComponents:Components={
      a({href,children}){const {options,numbers}=useContext(MarkdownContext);
        if(href?.startsWith('citation:')){const id=href.slice(9),source=options.source(id),number=numbers.get(id);return <button className={'citation-ref'+(source?'':' unavailable')} data-know={id} aria-label={source?`来源 ${number}：${options.sourceTitle(source)} · ${options.sourceLocation(source)}`:'引用原文未定位'} aria-haspopup="dialog" aria-expanded="false" title={source?options.sourceTitle(source)+' · '+options.sourceLocation(source):'原文摘录未保留'}>{number||'?'}</button>}
        if(href?.startsWith('artifact:')){const id=href.slice(11),artifact=options.artifact(id);return artifact?<a className="art" href={'/artifacts/'+artifact.artifact_id+'/file'} data-art={artifact.artifact_id}>{children}</a>:<span>{children}</span>}
        return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
      },
      img({src,alt}){const {options}=useContext(MarkdownContext),uri=src as string;if(!uri?.startsWith('artifact://'))return null;const id=uri.slice(11),artifact=options.artifact(id);return artifact?.kind==='plot'||artifact?.media_type?.startsWith('image/')?<span className="response-figure"><button className="response-figure-open" onClick={()=>options.openArtifact(id)} aria-label={'查看'+options.artifactTitle(artifact)}><img className="report-image" src={'/artifacts/'+id+'/file'} alt={alt||options.artifactTitle(artifact)}/></button><span className="figure-caption"><strong>{options.artifactTitle(artifact)}</strong>{alt&&alt!==options.artifactTitle(artifact)&&<span>{alt}</span>}{resultDescription(artifact)&&<span>{resultDescription(artifact)}</span>}</span></span>:<a className="art" href="#" data-art={id}>{alt||'查看产物'}</a>}
};
const markdownPlugins=[remarkGfm];
const outputURL=(url:string)=>url.startsWith('citation:')||url.startsWith('artifact:')||/^https?:/.test(url)?url:'';
const StreamingText=memo(function StreamingText({message,options,numbers,metadataVersion}:{message:Message;options:Options;numbers:Map<string,number>;metadataVersion:number}){
  const context=useMemo(()=>({options,numbers}),[options,numbers]);
  return <div className={'md response-text '+message.phase+(message.done?'':' streaming')} data-message-id={message.id} data-phase={message.phase}>
    <MarkdownContext.Provider value={context}><Markdown remarkPlugins={markdownPlugins} urlTransform={outputURL} components={markdownComponents}>{prepareMarkdown(message.text,numbers,options)}</Markdown></MarkdownContext.Provider>
  </div>;
});
const toolErrorText=(tool:Tool)=>tool.result.error.code==='bad_args'?'调用参数格式有误，本次未执行。':tool.result.error.message;
const ToolRow=memo(function ToolRow({tool,options,compact=false,showProgress=false}:{tool:Tool;options:Options;compact?:boolean;showProgress?:boolean}){
  const active=tool.status==='running'||tool.status==='preparing';
  const args=tool.status==='preparing'?'':options.formatArgs(tool.name,tool.args);
  const rejected=tool.status==='error'&&tool.result.error.code==='bad_args';
  const result=tool.status==='error'?toolErrorText(tool):tool.status==='done'?options.toolSummary(tool.name,tool.result):'';
  const status=rejected?'未执行':{preparing:'准备中',running:'进行中',done:'完成',error:'未完成',interrupted:'已中断'}[tool.status];
  const progress=showProgress?tool.progress:undefined;
  return <div className={'step '+(active?(progress?.length?'working':'run'):tool.status==='error'?'err':'done')+(compact?' compact':'')} data-tool-id={tool.id} data-status={tool.status}>
    {!compact&&<Icon name={progress?.length?toolIcon(tool.name):active?'progress_activity':tool.status==='error'?'error':tool.status==='interrupted'?'stop':'check'}/>}<div className="step-copy">{!compact&&<div className="step-heading"><span className="name">{options.toolLabel(tool.name)}</span><span className="step-status">{status}</span></div>}{compact&&active&&!progress?.length&&<div className="step-inline-status"><Icon name="progress_activity"/>{status}</div>}{args&&!progress?.length&&<div className="txt">{args}</div>}{progress?.length&&<div className="remote-progress" aria-label={tool.name==='run_python'?'脚本执行阶段':'执行阶段'}>{progress.map((stage,index)=>{const current=index===progress.length-1&&tool.status!=='done';return <div key={stage.stage} className={'remote-stage'+(current?' current':'')+(current&&tool.status==='running'?' active':'')}><Icon name={current?tool.status==='error'?'error':tool.status==='interrupted'?'stop':'progress_activity':'check'}/><span>{stage.label}{stage.detail&&<small>{stage.detail}</small>}</span></div>})}</div>}{result&&result!==args&&<div className="step-result">{result}</div>}</div>
    <button className="step-detail icon-btn" aria-label={`查看${options.toolLabel(tool.name)}的执行详情`} title="查看执行详情" onClick={()=>options.openTrace(tool.id)}><Icon name="arrow_right_alt"/></button>
  </div>;
});

type ToolGroup={type:'tools';id:string;tools:Tool[]};
function groupEntries(entries:Turn['entries'],options:Options){
  const groups:(Message|ToolGroup)[]=[];
  for(const entry of entries){
    if(entry.type==='message'){if(entry.text)groups.push(entry);continue}
    if(!options.toolVisible(entry.name))continue;
    const last=groups.at(-1);
    if(last?.type==='tools')last.tools.push(entry);
    else groups.push({type:'tools',id:entry.id,tools:[entry]});
  }
  return groups;
}
const toolIcon=(name:string)=>({list_files:'folder_open',read_file:'description',write_file:'description',edit_file:'description',save_to_workspace:'folder_open',read_document:'menu_book',read_artifact:'description',inspect_record:'troubleshoot',search_knowledge:'search',knowledge_graph:'hub',plot:'show_chart',export:'download',run_python:'terminal',route_model:'hub',list_models:'hub',check_model_compat:'rule'}[name]||'waves');
const OperationGroup=memo(function OperationGroup({group,options,engage,startedAt}:{group:ToolGroup;options:Options;engage:()=>void;startedAt?:number}){
  const [expanded,setExpanded]=useState(false);
  const active=group.tools.filter(tool=>tool.status==='running'||tool.status==='preparing');
  const failed=group.tools.filter(tool=>tool.status==='error').length;
  const interrupted=group.tools.filter(tool=>tool.status==='interrupted').length;
  const done=group.tools.filter(tool=>tool.status==='done').length;
  const current=active.at(-1),last=group.tools.at(-1)!;
  const names=[...new Set(group.tools.map(tool=>options.toolLabel(tool.name)))];
  const label=current&&!expanded?current.progress?.at(-1)?.label||`${current.status==='preparing'?'准备':'正在'}${options.toolLabel(current.name)}`:names.length>2?`${names[0]}、${names[1]}等`:names.join('、');
  const detail=current?(expanded?'':current.progress?.at(-1)?.detail||(current.status==='preparing'?'':options.formatArgs(current.name,current.args))):last.status==='done'?options.toolSummary(last.name,last.result):last.status==='error'?toolErrorText(last):'操作已中断';
  const status=failed?'有操作未完成':interrupted?'已中断':'';
  return <section className={'operation-group'+(current&&!expanded?' active':'')+(failed?' has-error':'')} data-group-id={group.id}>
    <button className="operation-summary" aria-expanded={expanded} aria-controls={'tools-'+group.id} onClick={()=>{setExpanded(!expanded);engage()}}>
      <Icon name={current&&!expanded?'progress_activity':failed?'error':toolIcon(last.name)}/><span className="operation-copy"><span className="operation-title">{label}</span>{detail&&<span className="operation-description" title={detail}>{detail}</span>}</span>{current&&<Elapsed start={startedAt}/>}<span className="operation-count">{status}</span><Icon name={expanded?'expand_more':'chevron_right'}/>
    </button>
    <div id={'tools-'+group.id} className="operation-details" hidden={!expanded}>{group.tools.map(tool=><ToolRow key={tool.id} tool={tool} options={options} compact={group.tools.length===1} showProgress={expanded}/>)}</div>
  </section>;
});
export const UserMessage=memo(({row,options}:{row:User;options:Options})=><>
  {row.questions.length>0&&<div className="answered-question">{row.questions.join('\n')}</div>}
  <div className="msg user"><div className="bubble"><div className="user-text">{row.text}</div>{row.attachments.length>0&&<div className="message-files">{row.attachments.map(file=><button type="button" key={file.id} className="message-file" onClick={()=>options.openAttachment(file.id)} title={'预览 '+file.name}>{file.modality==='image'?<img className="message-image-thumb" src={'/sessions/'+options.session()+'/attachments/'+file.id+'/file?view=1'} alt={file.name}/>:<Icon name={file.modality==='video'?'videocam':'description'}/>}<span>{file.name}</span></button>)}</div>}{row.references.length>0&&<div className="message-files">{row.references.map(file=><button type="button" key={file.manifest_id||file.workspace_id+':'+file.path} className="message-file reference-file" onClick={()=>options.openReference(file)} title={file.path||file.name}><Icon name={file.kind==='directory'?'folder_open':'description'}/><span>{file.path==='.'?'当前文件夹':file.name}</span></button>)}</div>}</div></div>
</>);

function turnReferences(row:Turn,options:Options){const ids:string[]=[];for(const entry of row.entries)if(entry.type==='message')for(const id of options.references(entry.text))if(options.source(id)&&!ids.includes(id))ids.push(id);return new Map(ids.map((id,index)=>[id,index+1]))}
export const AgentTurn=memo(function AgentTurn({row,options,toggle,engage,metadataVersion}:{row:Turn;options:Options;toggle:()=>void;engage:()=>void;metadataVersion:number}){
  const referenceKeys=[...turnReferences(row,options).keys()].join('|'),numbers=useMemo(()=>new Map(referenceKeys?referenceKeys.split('|').map((id,index)=>[id,index+1]):[]),[referenceKeys]),tools=row.entries.filter((entry):entry is Tool=>entry.type==='tool'&&options.toolVisible(entry.name));
  const process=row.entries.filter(entry=>entry.type==='message'?entry.phase==='commentary'&&entry.text.length>0:options.toolVisible(entry.name));
  const running=tools.filter(entry=>entry.status==='running'||entry.status==='preparing').at(-1),open=row.waiting||row.open===true;
  const groups=groupEntries(row.entries,options);
  const latestComment=process.filter((entry):entry is Message=>entry.type==='message').at(-1);
  const [commentPreview,setCommentPreview]=useState<string|undefined>(undefined),[recentCompletion,setRecentCompletion]=useState(false);
  const [reasoningPhase,setReasoningPhase]=useState<'visible'|'fading'|'hidden'>('hidden'),reasoningText=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const remaining=row.reasoning?REASONING_PREVIEW_MS-(Date.now()-row.reasoning.updatedAt):0;
    if(remaining<=0||row.waiting||row.stopped||row.error){setReasoningPhase('hidden');return}
    setReasoningPhase(remaining<=200?'fading':'visible');
    const fade=setTimeout(()=>setReasoningPhase('fading'),Math.max(0,remaining-200)),hide=setTimeout(()=>setReasoningPhase('hidden'),remaining);
    return()=>{clearTimeout(fade);clearTimeout(hide)};
  },[row.reasoning,row.waiting,row.stopped,row.error]);
  useLayoutEffect(()=>{if(reasoningText.current)reasoningText.current.scrollTop=reasoningText.current.scrollHeight},[row.reasoning?.text,reasoningPhase]);
  useEffect(()=>{if(!row.running||!latestComment){setCommentPreview(undefined);return}setCommentPreview(latestComment.id);const timer=setTimeout(()=>setCommentPreview(undefined),18000);return()=>clearTimeout(timer)},[row.running,latestComment?.id,latestComment?.text]);
  useEffect(()=>{if(row.running||!row.finishedAt||row.error||row.stopped){setRecentCompletion(false);return}setRecentCompletion(true);const timer=setTimeout(()=>setRecentCompletion(false),5000);return()=>clearTimeout(timer)},[row.running,row.finishedAt,row.error,row.stopped]);
  const answers=row.entries.filter((entry):entry is Message=>entry.type==='message'&&(entry.phase==='answer'||entry.phase==='partial'));
  const text=answers.map(entry=>entry.text).join('\n\n');
  const deliveries=workspaceDeliveries(row),saved=new Set(deliveries.flatMap(delivery=>delivery.files.map((file:any)=>file.artifact_id).filter(Boolean)));
  const artifacts=resultItems(row.artifacts,options.artifact).filter(item=>!saved.has(item.artifact_id));
  const writing=row.entries.some(entry=>entry.type==='message'&&entry.phase!=='commentary'&&!entry.done&&entry.text);
  const label=running?running.progress?.at(-1)?.label||`${running.status==='preparing'?'准备':'正在'}${options.toolLabel(running.name)}`:writing?'正在回复':'思考中';
  const toolActivityVisible=running&&row.open!==false;
  const publicProgressVisible=row.open!==false&&commentPreview===latestComment?.id&&Boolean(latestComment);
  const showProcess=process.length>0;
  const processLabel=row.running?'处理过程':row.stopped?'已停止':row.error||!answers.length?'查看处理过程':'已完成';
  const lastGroup=groups.filter((entry):entry is ToolGroup=>entry.type==='tools').at(-1);
  const connectionError=row.error&&/\b(?:read|write|dial) tcp\b|\bwsarecv\b/.test(row.error);
  const sourceGroups=new Map<string,{id:string;source:any;numbers:number[]}>();
  for(const [id,number] of numbers){const source=options.source(id),key=source.manifest_id||source.original_url||source.doc_id||source.title;const group=sourceGroups.get(key);if(group)group.numbers.push(number);else sourceGroups.set(key,{id,source,numbers:[number]})}
  return <article className={'msg agent'+(row.running?' is-running':'')+(showProcess?' has-process':'')} data-turn-id={row.id}><div className="bubble">
    {showProcess&&<div className="turn-process"><button className="activity-summary" onClick={toggle} aria-label="查看处理过程" aria-expanded={open} aria-controls={'entries-'+row.id}><span>{row.running?'分析过程':processLabel}</span>{!row.running&&<Elapsed start={row.startedAt} end={row.finishedAt}/>}<span className="activity-count">{tools.length>0?`${tools.length} 步`:''}</span><Icon name={open?'expand_more':'chevron_right'}/></button></div>}
    <div id={'entries-'+row.id} className={'turn-thread transcript-entries'+(open?' process-open':'')}>
      {groups.map(entry=>entry.type==='tools'?<div className={'process-entry tool-entry'+(row.open!==false&&(entry.tools.some(tool=>tool.status==='running'||tool.status==='preparing')||recentCompletion&&entry.id===lastGroup?.id)?' is-current':'')} key={entry.id} onPointerDown={engage} onFocusCapture={engage}><OperationGroup group={entry} options={options} engage={engage} startedAt={row.startedAt}/></div>:<div key={entry.id} className={entry.phase==='commentary'?'process-entry commentary-entry'+(row.open!==false&&commentPreview===entry.id?' is-current':''):'answer-entry'} onPointerDown={entry.phase!=='answer'?engage:undefined} onFocusCapture={entry.phase!=='answer'?engage:undefined}><StreamingText message={entry} options={options} numbers={numbers} metadataVersion={metadataVersion}/></div>)}
    </div>
    {row.reasoning&&reasoningPhase!=='hidden'&&<div className={'reasoning-preview'+(reasoningPhase==='fading'?' is-fading':'')} aria-label="模型思考"><span className="reasoning-label">思考</span><div className="reasoning-text" ref={reasoningText}>{row.reasoning.text}</div></div>}
    {(artifacts.length>0||deliveries.length>0)&&<button className="turn-results-link" aria-label={`查看本轮全部结果：${[artifacts.length?resultSummary(artifacts):'',deliveries.length?workspaceSummary(deliveries):''].filter(Boolean).join(' · ')}`} onClick={()=>options.openResults(row.id)}><Icon name="folder_open"/><span><strong>本轮结果</strong><small>{[artifacts.length?resultSummary(artifacts):'',deliveries.length?workspaceSummary(deliveries):''].filter(Boolean).join(' · ')}</small></span><span className="results-open-label">查看全部</span><Icon name="chevron_right"/></button>}
    {numbers.size>0&&answers.length>0&&<div className="turn-sources"><span className="sources-label">来源</span>{[...sourceGroups].map(([key,group])=><button key={key} className="source-chip" data-know={group.id} title={options.sourceTitle(group.source)+' · 引用 '+group.numbers.join('、')}><span className="source-number">{group.numbers.join(',')}</span><span className="source-chip-title">{options.sourceTitle(group.source)}</span></button>)}</div>}
    {row.running&&!row.waiting&&!toolActivityVisible&&!publicProgressVisible&&!writing&&reasoningPhase==='hidden'&&<div className="active-status" role="status" data-activity={running?'working':row.activity||'thinking'}><span className="working-dot" aria-hidden="true"/><span>{label}</span><Elapsed start={row.startedAt}/></div>}
    {row.waiting&&<div className="waiting-status" role="status"><Icon name="chat_bubble"/>等待你的回答</div>}
    {row.error&&<div className="error-box" role="alert"><Icon name="error"/><div className="error-copy"><strong>{connectionError?'连接中断':'请求未完成'}</strong><p>{connectionError?'模型服务连接中断，请重试。':row.error}</p>{connectionError&&<details className="error-details"><summary>查看错误详情</summary><p>{row.error}</p></details>}</div><button className="chip small" onClick={options.retry}>重试</button></div>}
    {row.stopped&&<div className="sysnote">已停止，已生成的内容保留在这里</div>}
    {text&&!row.running&&<div className="reply-actions"><button className="icon-btn" title="复制回复" aria-label="复制回复" onClick={()=>options.copy(text)}><Icon name="content_copy"/></button></div>}
  </div></article>;
});

export function mount(container:HTMLElement,options:Options){
  const store=createTranscriptStore();
  function Transcript(){const state=useSyncExternalStore(store.subscribe,store.getSnapshot);useEffect(options.changed,[state.contentVersion,state.metadataVersion]);return state.rows.length?<>{state.rows.map(row=>row.type==='user'?<UserMessage key={options.session()+row.id} row={row} options={options}/>:<AgentTurn key={options.session()+row.id} row={row} options={options} toggle={()=>store.toggle(row.id)} engage={()=>store.engage(row.id)} metadataVersion={state.metadataVersion}/>)}</>:<div className="empty" id="empty"><h1>想分析什么？</h1></div>}
  createRoot(container).render(<Transcript/>);
  return store;
}
