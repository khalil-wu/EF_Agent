
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let sessionId=null, streaming=false, mode='readonly', manifests=[], artifacts={};
let viewVersion=0, streamController=null, creating=null, submitting=false, uploading=false, navigating=false, followBottom=true, stopRequested=false;
let workspaceCatalog=[];
const expandedWorkspaces=JSON.parse(localStorage.getItem('eeg-workspace-folders')||'{}');
let sessionsCache=[],trashView=false,sessionListVersion=0,menuRecord=null,dialogAction=null;
let currentTitle='',currentParent='',activeSkillName='';
let skillCatalog=[],commandIndex=0,commandMatches=[];
let modelCatalog=[],modelTask='emotion',pendingInput=null;
let currentLLMModel='',defaultLLMModel='',llmModels=[],modelCapabilities={},changingModel=false;
let pendingFiles=[],pendingFilesBySession=new Map(),uploadTask=null;
const fileUploads=new WeakMap();
let transcript,liveCursor=null;
let resultScope='latest',selectedArtifact=null;
let currentWorkspaceID='';
let menuWorkspace=null;
let currentPanel='data',lastContentPanel='data';
const attachmentViewer=AttachmentPreview.mount($('#attachmentPreview'),{load:async id=>{if(typeof id!=='object')return api(`/sessions/${sessionId}/attachments/${id}/preview`);const result=await api(`/workspaces/${id.workspace}${id.kind==='directory'?'':'/preview'}?path=${encodeURIComponent(id.path)}`);return {...result,display_path:id.path}},markdown:text=>md(text)});
const eventTraces=new Map();
const workspaceMount=WorkspaceMount.mount({api,select:id=>newSession(false,id)});
const fileMentions=FileMentions.mount({input:$('#input'),menu:$('#mentionMenu'),chips:$('#referenceChips'),list:referenceSuggestions,scope:()=>sessionId+'|'+currentWorkspaceID,preview:openReference,changed:autosize});
const TOOL_LABEL={analyze_signal:'本地信号分析',workspace_quality:'批量检查信号质量',workspace_files:'整理工作区',write_file:'保存工作区文件',edit_file:'修改工作区文件',save_to_workspace:'保存分析结果到工作区',request_user_input:'补充分析信息',update_record_metadata:'登记数据说明',read_skill:'读取分析方法',list_files:'查找数据',read_file:'读取文件',read_document:'阅读文档',inspect_record:'检查数据结构',preprocess:'预处理',clean_artifacts:'去除伪迹',detect_transients:'定位瞬态候选',segment_signal:'划分时间窗',extract_features:'提取特征',save_emotion_assessment:'保存情绪判断',plot:'绘制图表',export:'导出结果',read_artifact:'读取结果',device_lookup:'核对设备',check_model_compat:'检查模型适用性',list_models:'查看识别模型',route_model:'选择识别模型',run_model:'执行识别模型',recognize_emotion:'识别情绪',run_python:'执行分析脚本',search_knowledge:'检索知识',knowledge_graph:'查询知识关系'};
let serviceState={worker:'unknown',model:'…'}, requestFailure='';
const KIND_ICON={report:'description',plot:'image',features:'grid_on',windows:'view_week',normalized_signal:'waves',table:'download',compat_report:'rule',prediction:'insights',emotion_assessment:'insights',aggregate:'stacked_line_chart'};
const KIND_LABEL={report:'研究报告',plot:'图',features:'特征',windows:'窗口表',normalized_signal:'预处理信号',table:'导出文件',prediction:'预测',emotion_assessment:'情绪判断',aggregate:'聚合'};
const BAND_COLOR={delta:'#5C6BC0',theta:'#26A69A',alpha:'#66BB6A',beta:'#FFA726',gamma:'#EF5350'};

/* ---------- helpers ---------- */
function snack(text,action){
  const box=$('#snack');box.replaceChildren();const label=document.createElement('span');label.textContent=text;box.appendChild(label);
  if(action){const button=document.createElement('button');button.textContent=action.label;button.onclick=()=>Promise.resolve(action.run()).catch(reportError);box.appendChild(button)}
  box.classList.add('show');clearTimeout(box._t);box._t=setTimeout(()=>box.classList.remove('show'),action?8000:3200);
}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function citationMarkup(id,numbers,label){
  const source=knowledge[id];
  if(!source)return `<button class="citation-ref unavailable" data-know="${esc(id)}" title="原文摘录未保留" aria-label="引用原文未定位">?</button>`;
  if(!numbers.has(id))numbers.set(id,numbers.size+1);
  const description=SourceCitations.title(source)+' · '+SourceCitations.location(source);
  return `<button class="citation-ref" data-know="${esc(id)}" aria-label="来源 ${numbers.get(id)}：${esc(description)}" aria-haspopup="dialog" aria-expanded="false" title="${esc(description)}"><span>${numbers.get(id)}</span></button>`;
}
function md(src,options={}){
  const citationNumbers=options.citationNumbers||new Map();
  const blocks=[]; src=src.replace(/```(\w*)\n([\s\S]*?)```/g,(m,l,c)=>{blocks.push(`<pre><code>${esc(c)}</code></pre>`);return `\u0000${blocks.length-1}\u0000`});
  const lines=src.split('\n'); let out='', i=0;
  const inline=t=>{
    const tokens=[];
    const token=html=>{tokens.push(html);return `\u0001${tokens.length-1}\u0001`};
    t=t.replace(/`([^`]+)`/g,(_,code)=>token(`<code>${esc(code)}</code>`))
      .replace(/!\[([^\]]*)\]\(artifact:\/\/(art_[0-9a-f]+)\)/g,(_,alt,id)=>token(options.images&&(!options.chat||artifacts[id]?.kind==='plot')?`<figure class="response-figure"><img class="report-image" src="/artifacts/${id}/file" alt="${esc(alt)}"><figcaption>${esc(alt||'分析图表')}</figcaption></figure>`:`<a class="art" href="#" data-art="${id}">${esc(alt||'查看图像')}</a>`))
      .replace(/\[([^\]]+)\]\((https?:[^\s)]+|artifact:\/\/art_[0-9a-f]+|\/artifacts\/art_[0-9a-f]+\/file(?:\?[^\s)]+)?|k_[0-9a-f]{6,})\)/g,(_,label,url)=>{
        if(url.startsWith('k_'))return options.citations===false?label:token(citationMarkup(url,citationNumbers,label));
        const local=url.match(/^\/artifacts\/(art_[0-9a-f]+)\/file(?:\?[^)]+)?$/);
        if(!url.startsWith('artifact:')&&!local)return token(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`);
        const id=local?local[1]:url.slice(11),title=label===id&&artifacts[id]?artifactTitle(artifacts[id]):label;
        return token(`<a class="art" href="#" data-art="${esc(id)}">${esc(title)}</a>`);
      });
    t=esc(t).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/(^|\W)\*([^*]+)\*(?=\W|$)/g,'$1<em>$2</em>');
    t=t.replace(/(?:artifact:\/\/)?\b(art_[0-9a-f]{6,})\b|\[(k_[0-9a-f]{6,})\]|\b(k_[0-9a-f]{6,})\b/g,(match,art,know,bare)=>art?token(`<a class="art" href="#" data-art="${art}">${esc(artifacts[art]?artifactTitle(artifacts[art]):'查看产物')}</a>`):options.citations===false?match:token(citationMarkup(know||bare,citationNumbers)));
    return t.replace(/\u0001(\d+)\u0001/g,(_,n)=>tokens[+n]);
  };
  while(i<lines.length){const l=lines[i]; if(/^\s*$/.test(l)){i++;continue} let m;
    if(/^!\[[^\]]*\]\(artifact:\/\/art_[0-9a-f]+\)\s*$/.test(l)){out+=inline(l);i++;continue}
    if((m=l.match(/^(#{1,3})\s+(.*)/))){out+=`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`;i++;continue}
    if(/^\s*\|.*\|\s*$/.test(l)&&i+1<lines.length&&/^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1])){const cells=r=>r.trim().replace(/^\||\|$/g,'').split('|').map(c=>inline(c.trim()));
      out+='<table><thead><tr>'+cells(l).map(c=>`<th>${c}</th>`).join('')+'</tr></thead><tbody>';i+=2;
      while(i<lines.length&&/^\s*\|.*\|\s*$/.test(lines[i])){out+='<tr>'+cells(lines[i]).map(c=>`<td>${c}</td>`).join('')+'</tr>';i++} out+='</tbody></table>';continue}
    if(/^\s*([-*•]|\d+[.)])\s+/.test(l)){const ol=/^\s*\d+[.)]/.test(l);out+=ol?'<ol>':'<ul>';
      while(i<lines.length&&/^\s*([-*•]|\d+[.)])\s+/.test(lines[i])){out+=`<li>${inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/,''))}</li>`;i++} out+=ol?'</ol>':'</ul>';continue}
    if(/^>\s?/.test(l)){let q=[];while(i<lines.length&&/^>\s?/.test(lines[i])){q.push(lines[i].replace(/^>\s?/,''));i++}out+=`<blockquote>${inline(q.join(' '))}</blockquote>`;continue}
    if(/^\u0000\d+\u0000$/.test(l.trim())){out+=blocks[+l.trim().slice(1,-1)];i++;continue}
    let p=[];while(i<lines.length&&!/^\s*$/.test(lines[i])&&!/^(#{1,3})\s|^\s*([-*•]|\d+[.)])\s|^>|^\s*\|.*\|\s*$|^!\[[^\]]*\]\(artifact:\/\/art_[0-9a-f]+\)\s*$|^\u0000/.test(lines[i])){p.push(lines[i]);i++}
    if(p.length)out+=`<p>${inline(p.join('\n')).replace(/\n/g,'<br>')}</p>`; else i++;}
  return out.replace(/\u0000(\d+)\u0000/g,(m,n)=>blocks[+n]);
}
function autosize(){const t=$('#input');t.style.height='auto';t.style.height=t.value?Math.min(240,t.scrollHeight)+'px':'';t.style.overflowY=t.value&&t.scrollHeight>240?'auto':'hidden';const hasReferences=fileMentions.references().length>0,stopping=streaming&&!t.value.trim()&&!hasReferences;$('#send').disabled=(!stopping&&!t.value.trim()&&!pendingFiles.length&&!hasReferences)||submitting||(navigating&&!uploadTask)||stopRequested;$('#input').disabled=(navigating&&!uploadTask)||stopRequested;$('#send').classList.toggle('steer',streaming);$('#send').title=stopRequested?'正在停止':stopping?'停止生成':streaming?'发送补充说明':uploadTask?'等待文件读入后发送 (Enter)':'发送 (Enter)';$('#send').setAttribute('aria-label',$('#send').title);$('#send .ms').textContent=stopping||stopRequested?'stop':'arrow_upward';$('#attach').disabled=streaming||submitting||navigating;$('#answerSubmit').disabled=streaming||submitting||navigating;$('#llmSelector').disabled=streaming||submitting||uploading||navigating||changingModel;updateCommands();fileMentions.update()}
async function api(url,options){const r=await fetch(url,options);const j=await r.json();if(!r.ok)throw new Error(j.worker?.error?.message||j.error?.message||j.error||j.detail||`请求失败 (${r.status})`);return j}
function reportError(error){snack(error.message)}
function resetSourceView(){const detail=$('#sourceDetail');detail.hidden=true;detail.replaceChildren();$('#knowledge').hidden=false;if($('#sourcePopover').matches(':popover-open'))$('#sourcePopover').hidePopover()}
function leaveView(){fileMentions.save(sessionId||'');resetSourceView();resetAttachmentView();if(sessionId)pendingFilesBySession.set(sessionId,pendingFiles);viewVersion++;navigating=true;streamController?.abort();streamController=null;streaming=false;submitting=false;stopRequested=false;followBottom=true;autosize()}
function rememberSession(id){localStorage.setItem('eeg-session',id);history.replaceState(null,'',`/?session=${encodeURIComponent(id)}`)}
function restoreStatus(){
  if(streaming)return;
  if(pendingInput){setConn('','等待你的回答');autosize();return}
  const connection=idleConnection(serviceState,requestFailure);setConn(connection.state,connection.text);
  autosize();
}
function idleConnection(health,error){
  if(error)return {state:'error',text:'上次请求失败'};
  if(health.worker!=='ok')return {state:'',text:health.worker==='unknown'?'连接中':'本地服务未就绪'};
  return {state:'ok',text:health.model};
}
function setConn(state,text){const cls='dot '+(state==='ok'?'on':state==='busy'?'busy':state==='error'?'error':'');$('#connDot').className=cls;$('#connChip').hidden=state==='ok'||state==='busy'||Boolean(pendingInput)&&state!=='error';$('#connText').textContent=state==='ok'?'工作区已连接':text}
function showTab(name){
  resetAttachmentView();
  if(currentPanel!=='trace')lastContentPanel=currentPanel;
  currentPanel=name;
  $('#detailBack').hidden=name!=='trace';
  const backLabel=({data:'返回文件',art:'返回结果',know:'返回来源',model:'返回识别模型',skill:'返回分析方法'})[lastContentPanel];
  $('#detailBack').title=backLabel;$('#detailBack').setAttribute('aria-label',backLabel);
  $('#trace').classList.toggle('is-detail',name==='trace');
  $('#panelTitle').textContent=({data:'会话文件',art:'分析结果',model:'识别模型',know:'引用来源',trace:'执行详情',skill:'分析方法'})[name];
  $$('.tab').forEach(tab=>{const selected=tab.dataset.tab===name;tab.classList.toggle('on',selected);tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;tab.setAttribute('aria-controls','pane-'+tab.dataset.tab)});
  $$('.pane').forEach(pane=>pane.classList.toggle('on',pane.id==='pane-'+name));
  $$('.workspace-nav [data-panel]').forEach(button=>button.classList.toggle('active',button.dataset.panel===name&&document.body.classList.contains('sheet-open')));
}
function badge(id,n){const b=$(id);b.textContent=n;b.hidden=true}
function lightbox(src){const lb=$('#lightbox');lb.querySelector('img').src=src;lb.style.display='flex'}
$('#lightbox').onclick=()=>$('#lightbox').style.display='none';
function scroll(){const selection=document.getSelection();if(followBottom&&!(selection?.toString()&&$('#chat').contains(selection.anchorNode))){const c=$('#chat');c.scrollTop=c.scrollHeight}}
$('#chat').addEventListener('load',e=>{if(e.target.tagName==='IMG')scroll()},true);
$('#chat').addEventListener('scroll',()=>{const c=$('#chat');followBottom=c.scrollHeight-c.scrollTop-c.clientHeight<100;$('#jumpToLatest').hidden=followBottom||Boolean($('#empty'))},{passive:true});
$('#jumpToLatest').onclick=()=>{followBottom=true;scroll();$('#jumpToLatest').hidden=true};
new ResizeObserver(([entry])=>{document.querySelector('main').style.setProperty('--composer-height',entry.borderBoxSize[0].blockSize+'px');scroll()}).observe($('.composer'));

function resizePanel(id,variable,min,max,direction){
  const handle=document.createElement('div');handle.id=id;handle.className='resize-handle';handle.tabIndex=0;handle.setAttribute('role','separator');handle.setAttribute('aria-orientation','vertical');handle.setAttribute('aria-label',direction===1?'调整导航宽度':'调整详情宽度');document.body.appendChild(handle);
  const width=()=>parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--'+variable));
  const set=value=>{const docked=innerWidth>1200,other=docked?(variable==='sidebar-w'?(document.body.classList.contains('sheet-open')?parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sheet-w')):0):(document.body.classList.contains('sidebar-collapsed')?0:parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w')))):0;const available=docked?innerWidth-other-520:innerWidth*.94;const next=Math.max(min,Math.min(max,available,value));document.documentElement.style.setProperty('--'+variable,next+'px');handle.setAttribute('aria-valuenow',String(Math.round(next)))};
  handle.setAttribute('aria-valuemin',String(min));handle.setAttribute('aria-valuemax',String(max));
  const stored=localStorage.getItem('eeg-'+variable);if(stored)set(parseFloat(stored));
  handle.onpointerdown=event=>{const start=event.clientX,initial=width();handle.setPointerCapture(event.pointerId);document.body.classList.add('resizing');handle.onpointermove=next=>set(initial+direction*(next.clientX-start));handle.onpointerup=()=>{handle.onpointermove=null;document.body.classList.remove('resizing');localStorage.setItem('eeg-'+variable,getComputedStyle(document.documentElement).getPropertyValue('--'+variable))}};
  handle.onkeydown=event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();set(width()+(event.key==='ArrowRight'?16:-16)*direction);localStorage.setItem('eeg-'+variable,getComputedStyle(document.documentElement).getPropertyValue('--'+variable))}};
  handle.ondblclick=()=>{set(variable==='sidebar-w'?240:380);localStorage.removeItem('eeg-'+variable)};
}
resizePanel('drawerResize','sidebar-w',200,320,1);resizePanel('sheetResize','sheet-w',320,520,-1);

/* ---------- session ---------- */
function resetView(){renderInputRequest(null);transcript.reset();$('#manifests').innerHTML='';$('#noData').style.display='';$('#artifacts').innerHTML='';$('#noArt').style.display='';$('#knowledge').innerHTML='';$('#facts').innerHTML='';$('#noKnow').style.display='';badge('#bData',0);badge('#bArt',0);badge('#bKnow',0);$('#trace').innerHTML='';$('#noTrace').style.display='';$('#activeSkill').textContent='自动选择方法';manifests=[];artifacts={};knowledge={};facts={};resultScope='latest';selectedArtifact=null;$('#resultSearch').value='';$('#resultList').replaceChildren();$('#resultCatalog').hidden=false;$('#resultDetail').hidden=true}
async function newSession(preserveComposer=false,workspaceId=currentWorkspaceID){
  const selectedWorkspace=workspaceId;
  const staged=preserveComposer?[...pendingFiles]:[];
  if(uploading||submitting||navigating){snack('文件或消息正在提交，请稍候');return}
  leaveView();const version=viewVersion;
  let j;try{j=await api('/sessions',{method:'POST'})}finally{if(version===viewVersion){navigating=false;autosize()}}if(version!==viewVersion)return;
  sessionId=j.session_id;requestFailure='';currentTitle='';currentParent='';setActiveSkill('');pendingFiles=staged;trashView=false;updateSessionListHeader();$('#branchLabel').hidden=true;rememberSession(sessionId);resetView();setConversationModel(j.llm_model);mode='readonly';
  if(selectedWorkspace)await api(`/sessions/${sessionId}/workspace`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id:selectedWorkspace})});
  currentWorkspaceID=selectedWorkspace;
  fileMentions.restore(sessionId,preserveComposer);
  if(selectedWorkspace){expandedWorkspaces[selectedWorkspace]=true;localStorage.setItem('eeg-workspace-folders',JSON.stringify(expandedWorkspaces))}
  if(!preserveComposer)$('#input').value='';renderPendingFiles();$('#sessionLabel').textContent='新分析';restoreStatus();refreshSessions().catch(reportError);$('#input').focus();
}
async function ensureSession(){if(!sessionId){if(!creating)creating=newSession(true).finally(()=>creating=null);await creating}}
async function refreshSessions(){
  const version=++sessionListVersion;
  const [list,folders]=await Promise.all([api('/sessions'+(trashView?'?view=trash':'')),api('/workspaces')]);
  if(version!==sessionListVersion)return;
  sessionsCache=list;workspaceCatalog=folders;renderSessionList();
}
function updateSessionListHeader(){
  $('#sessionListTitle').textContent=trashView?'回收站':'工作区';
  $('#trashToggle').title=trashView?'返回分析会话':'打开回收站';$('#trashToggle').setAttribute('aria-label',$('#trashToggle').title);
  $('#trashToggle .ms').textContent=trashView?'reply':'delete_sweep';
  $('#sessionSearch').placeholder=trashView?'搜索已删除会话':'搜索会话';
}
function renderSessionList(){
  const query=$('#sessionSearch').value.trim().toLowerCase(),list=sessionsCache.filter(record=>(record.title||'新分析').toLowerCase().includes(query));
  const box=$('#sesList');box.replaceChildren();
  function append(record,target){
    const title=record.title||'新分析',row=document.createElement('div');row.className='ses'+(record.session_id===sessionId?' on':'')+((record.running||record.session_id===sessionId&&streaming)?' running':'');row.dataset.sessionId=record.session_id;
    const open=document.createElement('button');open.className='session-open';open.title=title;open.setAttribute('aria-current',record.session_id===sessionId?'page':'false');open.innerHTML='<span class="tt"><span class="t"></span></span>';open.querySelector('.t').textContent=title;open.disabled=trashView;open.onclick=()=>{closeDrawer();resumeSession(record.session_id).catch(reportError)};
    const more=document.createElement('button');more.className='icon-btn session-more';more.title=title+'的会话操作';more.setAttribute('aria-label',more.title);more.innerHTML='<span class="ms">more_horiz</span>';more.onclick=()=>openSessionMenu(record,more);
    row.append(open,more);target.appendChild(row);
  }
  if(trashView){list.forEach(record=>append(record,box));if(!list.length)box.innerHTML='<div class="list-empty">回收站为空</div>';return}
  for(const workspace of workspaceCatalog){
    const children=list.filter(record=>record.workspace_id===workspace.workspace_id);
    if(query&&!children.length&&!workspace.name.toLowerCase().includes(query))continue;
    const open=expandedWorkspaces[workspace.workspace_id]??true;
    const group=document.createElement('section');group.className='workspace-group';group.dataset.workspaceId=workspace.workspace_id;
    const header=document.createElement('div');header.className='workspace-folder';header.setAttribute('aria-label','工作区：'+workspace.name);
    header.oncontextmenu=event=>{event.preventDefault();openWorkspaceMenu(workspace,header,{x:event.clientX,y:event.clientY})};
    header.onkeydown=event=>{if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10'){event.preventDefault();openWorkspaceMenu(workspace,header)}};
    const label=document.createElement('button');label.className='workspace-folder-label';label.title=workspace.root;label.setAttribute('aria-expanded',String(open));label.setAttribute('aria-controls','workspace-chats-'+workspace.workspace_id);label.innerHTML=`<span class="ms">${IconSystem.svg(open?'folder_open':'folder')}</span><span>${esc(workspace.name)}</span>`;
    label.onclick=()=>{expandedWorkspaces[workspace.workspace_id]=!open;localStorage.setItem('eeg-workspace-folders',JSON.stringify(expandedWorkspaces));renderSessionList()};
    const create=document.createElement('button');create.className='icon-btn workspace-new';create.title='在'+workspace.name+'中新建对话';create.setAttribute('aria-label',create.title);create.innerHTML='<span class="ms">add</span>';create.onclick=()=>newSession(false,workspace.workspace_id).catch(reportError);
    header.append(label,create);group.appendChild(header);
    const chats=document.createElement('div');chats.className='workspace-chats';chats.id='workspace-chats-'+workspace.workspace_id;chats.hidden=!open;children.forEach(record=>append(record,chats));group.appendChild(chats);
    box.appendChild(group);
  }
  const independent=list.filter(record=>!record.workspace_id||!workspaceCatalog.some(workspace=>workspace.workspace_id===record.workspace_id));
  independent.forEach(record=>append(record,box));
  if(!workspaceCatalog.length&&!independent.length)box.innerHTML='<div class="list-empty">挂接一个文件夹，开始新的分析。</div>';
}
function openWorkspaceMenu(record,anchor,point){
  menuWorkspace=record;const menu=$('#workspaceMenu');
  const busy=sessionsCache.some(session=>session.workspace_id===record.workspace_id&&session.running)||currentWorkspaceID===record.workspace_id&&(streaming||uploading||submitting||navigating);
  $('#workspaceMenuTitle').textContent=record.name;$('#removeWorkspace').disabled=busy;
  $('#workspaceMenuNote').textContent=busy?'当前正在处理，完成后可移除。':'保留本地文件和已有会话。';
  menu.showPopover();const rect=anchor.getBoundingClientRect();
  menu.style.left=Math.max(12,Math.min(point?point.x:rect.left,innerWidth-menu.offsetWidth-12))+'px';
  menu.style.top=Math.max(12,Math.min(point?point.y:rect.bottom+6,innerHeight-menu.offsetHeight-12))+'px';
  if(!busy)$('#removeWorkspace').focus();
}
async function removeWorkspace(record){
  await api(`/workspaces/${record.workspace_id}`,{method:'DELETE'});
  if(currentWorkspaceID===record.workspace_id){currentWorkspaceID='';resetAttachmentView()}
  fileMentions.removeWorkspace(record.workspace_id);
  delete expandedWorkspaces[record.workspace_id];localStorage.setItem('eeg-workspace-folders',JSON.stringify(expandedWorkspaces));
  await refreshSessions();fileMentions.update();snack('已移除 '+record.name+'，已有会话保留');
}
function openSessionMenu(record,anchor){
  menuRecord=record;const menu=$('#sessionMenu'),deleted=Boolean(record.deleted_at);
  $('#menuTitle').textContent=record.title||'新分析';
  menu.querySelectorAll('[data-action]').forEach(button=>{
    button.hidden=deleted?button.dataset.action!=='restore':button.dataset.action==='restore';
    button.disabled=Boolean(record.running)&&['rename','clone','delete'].includes(button.dataset.action);
    if(button.dataset.action.startsWith('export-')){const format=button.dataset.action.slice(7);button.href=`/sessions/${record.session_id}/export?format=${format}`;button.download=`EFAgent-${record.session_id}.${format}`}
  });
  menu.querySelectorAll('.menu-separator').forEach(div=>div.hidden=deleted);
  menu.showPopover();const rect=anchor.getBoundingClientRect();
  menu.style.left=Math.max(12,Math.min(rect.right-menu.offsetWidth,innerWidth-menu.offsetWidth-12))+'px';
  menu.style.top=Math.max(12,Math.min(rect.bottom+6,innerHeight-menu.offsetHeight-12))+'px';
  menu.querySelector('[role=menuitem]:not([hidden]):not(:disabled)').focus();
}
function openSessionDialog(action,record){
  dialogAction={action,record};const rename=action==='rename';
  $('#dialogTitle').textContent=rename?'重命名分析会话':'删除这条会话？';
  $('#dialogDescription').textContent=rename?'用数据名称或研究目标命名，方便以后继续分析。':`“${record.title||'新分析'}”将移入回收站，可以恢复。数据文件和分析产物会保留，已有分支仍可使用。`;
  $('#renameLabel').hidden=!rename;$('#renameInput').required=rename;$('#renameInput').value=record.title||'';
  $('#dialogError').hidden=true;$('#dialogSubmit').disabled=false;$('#dialogSubmit').textContent=rename?'保存名称':'移入回收站';$('#dialogSubmit').classList.toggle('danger',!rename);
  $('#sessionDialog').showModal();if(rename){$('#renameInput').focus();$('#renameInput').select()}else $('#dialogCancel').focus();
}
function clearCurrentSession(){
  leaveView();sessionId=null;pendingFiles=[];renderPendingFiles();currentTitle='';currentParent='';setActiveSkill('');requestFailure='';mode='readonly';
  localStorage.removeItem('eeg-session');history.replaceState(null,'','/');resetView();$('#sessionLabel').textContent='新分析';$('#branchLabel').hidden=true;fileMentions.restore('');$('#input').value='';
navigating=false;closeSheet();restoreStatus();
}
async function restoreSessionRecord(id,open=true){
  await api(`/sessions/${id}/restore`,{method:'POST'});trashView=false;updateSessionListHeader();$('#sessionSearch').value='';await refreshSessions();if(open){closeDrawer();await resumeSession(id)}snack('会话已恢复');
}
async function performMenuAction(action,record){
  if(uploading||submitting||navigating){snack('文件或消息正在提交，请稍候');return}
  if(action==='rename'||action==='delete'){openSessionDialog(action,record);return}
  if(action==='restore'){await restoreSessionRecord(record.session_id);return}
  if(action==='clone'){
    const version=viewVersion;snack('正在复制数据上下文与分析记录…');
    const result=await api(`/sessions/${record.session_id}/clone`,{method:'POST'});
    trashView=false;updateSessionListHeader();await refreshSessions();
    if(version===viewVersion){closeDrawer();await resumeSession(result.session_id)}snack('已创建独立分析分支');return;
  }

}
function openDrawer(){if(innerWidth>900){const collapsed=document.body.classList.toggle('sidebar-collapsed');localStorage.setItem('eeg-sidebar-collapsed',String(collapsed));$('#drawerBtn').setAttribute('aria-expanded',String(!collapsed));return}closeSheet();refreshSessions().catch(reportError);$('#scrim').style.display='block';$('#drawer').classList.add('open');$('#drawerBtn').setAttribute('aria-expanded','true')}
function closeDrawer(){$('#drawer').classList.remove('open');$('#drawerBtn').setAttribute('aria-expanded',String(innerWidth>900&&!document.body.classList.contains('sidebar-collapsed')));if(!(narrow()&&document.body.classList.contains('sheet-open')))$('#scrim').style.display='none'}
async function resumeSession(id){
  if(uploading||submitting||uploadTask){snack('文件或消息正在提交，请稍候');return}
  leaveView();const version=viewVersion;
  let j;try{j=await api('/sessions/'+id)}finally{if(version===viewVersion){navigating=false;autosize()}}if(version!==viewVersion)return;
  sessionId=id;fileMentions.restore(id);pendingFiles=pendingFilesBySession.get(id)||[];restorePendingFiles(j);renderPendingFiles();rememberSession(id);renderSession(j);refreshSessions().catch(reportError);
  if(j.running)watchSession(id,version);
}
function renderSession(j){
  currentWorkspaceID=j.workspace_id||'';
  resetView();setConversationModel(j.llm_model);currentTitle=j.title||'';currentParent=j.parent_session_id||'';$('#sessionLabel').textContent=currentTitle||'新分析';$('#branchLabel').hidden=!currentParent;
  for(const m of j.manifests||[])addManifest(m,j.previews?.[m.manifest_id]);
  for(const a of j.artifacts||[])addArtifactCard(a);
  for(const source of Object.values(j.sources||{}))addKnowledge(source,'snapshot');
  for(const source of j.knowledge||[])addKnowledge(source,'auto');
  for(const fact of j.knowledge_facts||[])addFact(fact);
  eventTraces.clear();
  for(const message of j.messages||[]){
    if(message.role==='assistant')for(const call of message.tool_calls||[]){const args=call.arguments_error?{}:parseArgs(call.function.arguments),entry=traceEv(call.arguments_error?'err':'run',TOOL_LABEL[call.function.name]||call.function.name,call.arguments_error||formatToolArgs(call.function.name,args),args);entry.toolName=call.function.name;entry.argumentsError=call.arguments_error;if(call.arguments_error){entry.querySelector('.st').textContent='未执行';traceDetail(entry,'未解析的调用参数',call.function.arguments)}eventTraces.set(call.id,entry)}
    if(message.role==='tool'){
      const result={...JSON.parse(message.content),...message.tool_presentation},entry=eventTraces.get(message.tool_call_id);
      if(entry){entry.className='ev '+(result.status==='error'?'err':'ok');entry.querySelector('.st').textContent=entry.argumentsError?'未执行':result.status==='error'?'未完成':'完成';traceDetail(entry,'完整结果',result).dataset.toolResult='true'}
      for(const source of result.chunks||[])addKnowledge(source,'search');for(const fact of result.facts||[])addFact(fact);
    }
  }
  setActiveSkill(j.active_skill||'');if(j.mode)mode=j.mode;
  transcript.hydrate(j);renderResultCatalog();liveCursor=j.live?{run_id:j.live.run_id,sequence:0}:null;streaming=j.running;stopRequested=false;requestFailure=j.last_error||'';
  for(const event of j.live?.events||[])consumeEngineEvent(event);
  if(j.live)liveCursor.sequence=j.live.sequence;
  if(!streaming)for(const entry of eventTraces.values())if(entry.classList.contains('run')){entry.className='ev stopped';entry.querySelector('.st').textContent='已中断'}
  renderInputRequest(j.pending_input||null);autosize();
  if(streaming)setConn('busy','正在分析');else restoreStatus();
}

function consumeEngineEvent(event){
  if(liveCursor?.run_id===event.run_id&&event.sequence<=liveCursor.sequence)return;
  if(event.run_id)liveCursor={run_id:event.run_id,sequence:event.sequence};
  if(['error','stopped','session_saved'].includes(event.type)){
    stopRequested=false;
    for(const entry of eventTraces.values())if(entry.classList.contains('run')){entry.className='ev stopped';entry.querySelector('.st').textContent='已中断'}
  }
  switch(event.type){
    case 'tool_call_started':{
      if(!eventTraces.has(event.tool_call_id)){const entry=traceEv('run',TOOL_LABEL[event.tool_name]||event.tool_name,'准备参数');entry.toolName=event.tool_name;entry.querySelector('.st').textContent='准备中';eventTraces.set(event.tool_call_id,entry)}
      break;
    }
    case 'tool_execution_start':{
      const args=parseArgs(event.args);let entry=eventTraces.get(event.tool_call_id);
      if(!entry)entry=traceEv('run',TOOL_LABEL[event.tool_name]||event.tool_name,formatToolArgs(event.tool_name,args),args);
      entry.toolName=event.tool_name;
      entry.querySelector('[data-tool-args]')?.remove();traceDetail(entry,'参数',args).dataset.toolArgs='true';
      entry.hidden=false;$('#noTrace').style.display='none';entry.className='ev run';entry.querySelector('.sub').textContent=formatToolArgs(event.tool_name,args);
      entry.querySelector('.st').textContent='运行中';eventTraces.set(event.tool_call_id,entry);setConn('busy',TOOL_LABEL[event.tool_name]||'正在分析');break;
    }
    case 'tool_execution_progress':{const entry=eventTraces.get(event.tool_call_id);entry.querySelector('.st').textContent=event.result.label;entry.querySelector('.sub').textContent=event.result.detail;setConn('busy',event.result.label);break}
    case 'tool_execution_end':{
      let entry=eventTraces.get(event.tool_call_id);
      if(!entry){entry=traceEv(event.is_error?'err':'ok',TOOL_LABEL[event.tool_name]||event.tool_name,'');eventTraces.set(event.tool_call_id,entry)}
      entry.toolName=event.tool_name;
      entry.argumentsError=event.arguments_error;
      entry.hidden=false;$('#noTrace').style.display='none';entry.className='ev '+(event.is_error?'err':'ok');entry.querySelector('.st').textContent=event.is_error?'未完成':'完成';entry.querySelector('[data-tool-result]')?.remove();traceDetail(entry,'完整结果',event.result).dataset.toolResult='true';
      if(event.arguments_error){entry.querySelector('.st').textContent='未执行';traceDetail(entry,'未解析的调用参数',event.raw_arguments)}
      if(!event.is_error){if(event.result.manifest)addManifest(event.result.manifest);for(const manifest of event.result.manifests||[])addManifest(manifest)}
      for(const source of event.result.chunks||[])addKnowledge(source,'search');
      for(const fact of event.result.facts||[])addFact(fact);
      break;
    }
    case 'artifact_ready':addArtifactCard(event.result);break;
    case 'knowledge_retrieved':{const result=event.result||{};for(const source of Array.isArray(result)?result:result.chunks||[])addKnowledge(source,'auto');for(const fact of result.facts||[])addFact(fact);break}
    case 'user_input_requested':renderInputRequest(event.result.request);break;
    case 'skill_activated':setActiveSkill(event.skill);traceEv('skill','方法',SKILL_NAMES[event.skill]||event.skill);break;
    case 'reflection_created':traceEv('reflect','继续分析',event.reason);break;
    case 'steering_injected':traceEv('skill','补充说明',event.text);break;
    case 'compaction_start':traceEv('run','整理上下文','');break;
    case 'compaction_end':traceEv('ok','上下文已整理','');break;
    case 'error':requestFailure=event.text;streaming=false;traceEv('err','请求未完成',event.text);restoreStatus();break;
    case 'stopped':streaming=false;restoreStatus();break;
    case 'session_saved':streaming=false;streamController=null;restoreStatus();refreshSessions().catch(reportError);break;
  }
  transcript.apply(event);
  if(['artifact_ready','session_saved'].includes(event.type))renderResultCatalog();
}

async function consumeEventStream(response,version){
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',complete=false;
  while(true){
    const {done,value}=await reader.read();if(done)break;
    if(version!==viewVersion){await reader.cancel();return}
    buffer+=decoder.decode(value,{stream:true}).replace(/\r/g,'');let end;
    while((end=buffer.indexOf('\n\n'))>=0){
      const block=buffer.slice(0,end);buffer=buffer.slice(end+2);
      const data=block.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5)).join('\n');if(!data)continue;
      const event=JSON.parse(data);consumeEngineEvent(event);complete=complete||['error','stopped','session_saved'].includes(event.type);
      if(event.type==='session_saved'){await reader.cancel();return}
    }
  }
  if(version===viewVersion&&!complete)throw new Error('实时连接已中断，可以重新打开会话继续查看');
}

async function watchSession(id,version){
  streamController=new AbortController();
  try{
    const response=await fetch(`/sessions/${id}/events?run_id=${encodeURIComponent(liveCursor.run_id)}&after=${liveCursor.sequence}`,{signal:streamController.signal});
    if(version!==viewVersion)return;
    if(response.status===409){const snapshot=await api('/sessions/'+id);if(version!==viewVersion)return;renderSession(snapshot);if(snapshot.running)return watchSession(id,version);return}
    if(!response.ok){const error=await response.json();throw new Error(error.error)}
    await consumeEventStream(response,version);
  }catch(error){if(version!==viewVersion)return;streaming=false;stopRequested=false;requestFailure=error.message;transcript.finish(error.message);restoreStatus()}
}


const STEP_LABEL={pick_eeg:'保留 EEG 通道',pick_channels:'选择通道',rereference:'重设参考',notch:'工频陷波',bandpass:'带通滤波',resample:'重采样',remove_baseline:'去基线',normalize:'标准化'};
const FEATURE_LABEL={psd:'功率谱',bandpower:'频带功率',de:'差分熵',hjorth:'Hjorth 参数',band_stats:'频带统计',spectral_entropy:'谱熵',asymmetry:'额叶不对称',connectivity:'连接性'};
const PLOT_LABEL={raw:'EEG 波形',psd:'功率谱',bandpower_timeline:'频带功率趋势',timeline:'时间趋势'};
function parseArgs(raw){return typeof raw==='string'?JSON.parse(raw):(raw||{})}
function preprocessDescription(step){
  const name=STEP_LABEL[step.op]||step.op;
  if(step.op==='notch')return `${name} · ${step.freq} Hz`;
  if(step.op==='bandpass')return `${name} · ${step.low}–${step.high} Hz`;
  if(step.op==='resample')return `${name} · ${step.target_hz} Hz`;
  if(step.op==='pick_channels')return `${name} · ${(step.channels||[]).join('、')}`;
  if(step.op==='rereference')return `${name} · ${step.method==='car'?'平均参考':step.channel||step.method}`;
  if(step.op==='remove_baseline')return `${name} · ${step.seconds} 秒`;
  if(step.op==='normalize')return `${name} · ${step.method}`;
  return name;
}
function formatToolArgs(name,a){
  const range=a.start_s!=null||a.end_s!=null?` · ${a.start_s??0}–${a.end_s??'末尾'} 秒`:'';
  switch(name){
    case 'analyze_signal':return a.emotion_review?'质量、频谱与情绪特征审阅':'质量、频谱与特征';
    case 'workspace_quality':return [a.path||'整个工作区',a.pattern].filter(Boolean).join(' · ');
    case 'workspace_files':return ({list:'浏览目录',inspect:'读取文件结构',exclude:'排除分析',include:'恢复参与',mkdir:'新建文件夹',rename:'重命名',move:'移动文件',trash:'移入回收区',restore:'恢复文件'})[a.action]+((a.paths||[]).length?' · '+a.paths.join('、'):'');
    case 'read_skill':return SKILL_NAMES[a.name]||a.name;
    case 'list_files':return `查找${a.kind==='video'?'视频':'脑电'}文件${a.pattern?' · '+a.pattern:''}`;
    case 'read_file':return `${a.path} · 第 ${a.offset||1} 行起`;
    case 'read_document':return [manifests.find(({m})=>m.manifest_id===a.manifest_id)?.m.original_name,`第 ${a.page||1} 页起 · 最多 ${a.limit||3} 页`].filter(Boolean).join(' · ');
    case 'inspect_record':return a.path||manifests.find(({m})=>m.manifest_id===a.manifest_id)?.m.original_name||'读取数据结构';
    case 'preprocess':return (a.steps||[]).map(preprocessDescription).join(' → ');
    case 'clean_artifacts':return ({bad_channels:'检查坏道',asr:'伪迹子空间重建',ica_iclabel:'ICA 成分识别'})[a.method]||a.method;
    case 'segment_signal':return `${a.window_s} 秒窗口 · ${a.stride_s||a.window_s} 秒步长${range}`;
    case 'extract_features':return (a.features||[]).map(x=>FEATURE_LABEL[x]||x).join('、');
    case 'save_emotion_assessment':return a.assessment_status==='hypothesis'?'候选假设 · '+a.label:'记录测量发现 · 情绪未评估';
    case 'plot':return `${PLOT_LABEL[a.kind]||a.kind}${range}`;
    case 'export':return a.kind==='python'?'导出复现脚本':`导出 ${String(a.kind||'').toUpperCase()}`;
    case 'read_artifact':return `查看${FEATURE_LABEL[a.part]||a.part||'摘要'}`;
    case 'run_python':return a.label||'运行 Python 分析脚本';
    case 'write_file':case 'edit_file':case 'save_to_workspace':return a.path;
    case 'search_knowledge':return a.query||'查询研究依据';
    case 'knowledge_graph':return a.entity||`${a.path_from||''} → ${a.path_to||''}`;
    case 'device_lookup':return [a.vendor,a.model].filter(Boolean).join(' ');
    case 'request_user_input':return a.questions.map(q=>q.question).join(' · ');
    case 'update_record_metadata':return '保存用户确认的数据参数';
    case 'check_model_compat':return '比较数据与模型输入要求';
    case 'list_models':return ({emotion:'情绪识别',sleep_stage:'睡眠分期',embedding:'EEG 表征'})[a.task]||'查看模型目录';
    case 'route_model':return (({emotion:'情绪识别',sleep_stage:'睡眠分期',embedding:'EEG 表征'})[a.task]||a.task)+' · 自动核对输入';
    case 'run_model':return '执行识别'+range+(a.video_manifest_id?' · 使用脑电与配对视频':' · 使用脑电');
    case 'recognize_emotion':return (a.video_manifest_id?'使用脑电与配对视频':'使用脑电')+range;
    default:return '';
  }
}
/* ---------- trace ---------- */
function traceEv(cls,title,sub,detail){$('#noTrace').style.display='none';const e=document.createElement('div');e.className='ev '+cls;e.innerHTML=`<div class="h"><b></b><span class="sub"></span><span class="st"></span></div>`;e.querySelector('b').textContent=title;e.querySelector('.sub').textContent=sub||'';if(detail!==undefined)traceDetail(e,'参数',detail).dataset.toolArgs='true';$('#trace').appendChild(e);return e}
function durText(ms){return ms<1000?`${Math.round(ms)} ms`:ms<60000?`${(ms/1000).toFixed(1)} s`:`${Math.floor(ms/60000)} 分 ${Math.round(ms%60000/1000)} 秒`}
function traceDetail(e,label,obj,opts){
  let parent=e;
  if(label==='参数'||label==='完整结果'||label==='未解析的调用参数'){
    if(label!=='未解析的调用参数')e[label==='参数'?'executionArgs':'executionResult']=obj;
    let technical=e.querySelector('.execution-tech');
    if(!technical){technical=document.createElement('details');technical.className='execution-tech';technical.innerHTML='<summary><span class="ms">chevron_right</span>技术详情</summary>';e.appendChild(technical)}
    parent=technical;
  }
  const d=document.createElement('details');d.innerHTML=`<summary><span class="ms">chevron_right</span><span class="lb"></span><span class="lines"></span></summary><pre class="raw"></pre>`;d.querySelector('.lb').textContent=label==='参数'?'调用参数 JSON':label==='完整结果'?'工具响应 JSON':label;
  const full=typeof obj==='string'?obj:JSON.stringify(obj,null,2),lines=full.split('\n'),lim=(opts&&opts.preview)||14,pre=d.querySelector('pre');
  const short=()=>lines.slice(0,lim).join('\n');
  d.querySelector('.lines').textContent=lines.length>lim?` · ${lines.length} 行`:'';
  pre.textContent=lines.length>lim?short():full;
  if(lines.length>lim){const more=document.createElement('div');more.className='moreline';more.innerHTML=`<span class="ms">unfold_more</span>还有 ${lines.length-lim} 行`;d.appendChild(more);
    d.addEventListener('toggle',()=>{pre.textContent=d.open?full:short();more.hidden=d.open})}
  parent.appendChild(d);
  if(e.dataset.executionFocused)renderExecutionOverview(e);
  return d}
function renderExecutionOverview(entry){
  entry.querySelector('.execution-overview')?.remove();
  const overview=document.createElement('div');overview.className='execution-overview';
  const description=entry.argumentsError||entry.executionResult?.error?.message||(entry.executionResult?toolResultSummary(entry.toolName,entry.executionResult):entry.querySelector('.sub').textContent);
  overview.innerHTML=(description?`<p class="execution-description">${esc(description)}</p>`:'')+ExecutionDetails.render({name:entry.toolName,args:entry.executionArgs||{},result:entry.executionResult},{manifests:manifests.map(({m})=>m),artifacts});
  entry.querySelector('.h').after(overview);
}
function openExecutionDetails(toolId){
  const entry=eventTraces.get(toolId);
  $('#trace [data-execution-focused]')?.removeAttribute('data-execution-focused');entry.hidden=false;entry.dataset.executionFocused='true';
  showTabAndOpen('trace');$('#noTrace').style.display='none';renderExecutionOverview(entry);$('.sheet-body').scrollTop=0;
}
function toolResultSummary(name,result){if(!result||typeof result!=='object')return typeof result==='string'?result.slice(0,120):'';
  const r=result,m=r.manifest||{},s=r.summary||{};
  switch(name){
    case 'analyze_signal':return `${s.n_windows} 个窗口 · 图表与 CSV 已保存`;
    case 'workspace_quality':return `${s.file_count} 个文件 · ${s.measured} 个试次已测量${s.not_measured?' · '+s.not_measured+' 个试次未完成检查':''}`;
    case 'write_file':case 'edit_file':case 'save_to_workspace':return `${r.relative_path} · 已保存`;
    case 'inspect_record':return [m.n_channels&&`${m.n_channels} 通道`,m.sample_rate_hz&&`${m.sample_rate_hz} Hz`,m.duration_s&&`${(m.duration_s).toFixed(1)} s`,m.source_format].filter(Boolean).join(' · ');
    case 'preprocess':{const sz=Array.isArray(s.shape)?`${s.shape[0]}×${s.shape[1]}`:(s.n_channels&&s.n_samples?`${s.n_channels}×${s.n_samples}`:'');return [sz,s.sample_rate_hz&&`${s.sample_rate_hz} Hz`,s.duration_s&&`${s.duration_s} s`,(s.steps||[]).length&&`${s.steps.length} 步处理`].filter(Boolean).join(' · ')}
    case 'segment_signal':return [s.n_windows&&`${s.n_windows} 个窗口`,s.window_s&&`窗长 ${s.window_s} s`,s.stride_s&&`步长 ${s.stride_s} s`].filter(Boolean).join(' · ');
    case 'extract_features':return [s.n_windows&&`${s.n_windows} 窗`,s.n_channels&&`${s.n_channels} 通道`,(s.features||[]).length&&`${s.features.length} 类特征`].filter(Boolean).join(' · ');
    case 'save_emotion_assessment':return [s.assessment_status==='hypothesis'?'候选假设 · '+s.label:'情绪未评估',s.coverage&&`${s.coverage.covered_duration_s} 秒证据`,s.prediction_count&&`${s.prediction_count} 段记录`].filter(Boolean).join(' · ');
    case 'clean_artifacts':return cleanSummary(s);
    case 'search_knowledge':return `${(r.chunks||[]).length} 块资料 · ${(r.facts||[]).length} 条关系`;
    case 'knowledge_graph':return r.entity?`${r.entity.name}：${(r.facts||[]).length} 条关系`:(r.path?`路径 ${r.path.length} 跳`:'');
    case 'list_files':return `${(r.files||[]).length} 个文件`;
    case 'read_file':return r.start_line?`第 ${r.start_line}–${r.end_line} 行 · ${(((r.content||'').split('\n')).length)} 行内容`:'';
    case 'read_document':return `${(r.pages||[]).length} 页`;
    case 'read_artifact':return r.rows?`${r.rows.length} 行 · 共 ${r.total??r.rows.length}`:'';
    case 'export':return [(r.summary||{}).format,r.summary?.rows&&`${r.summary.rows} 行`].filter(Boolean).join(' · ');
    case 'plot':return `图已生成（${(r.summary||{}).kind||''}）`;
    case 'check_model_compat':return '已检查识别方法的输入条件';
    case 'request_user_input':return '等待用户回答';
    case 'update_record_metadata':return '用户确认的信息已登记';
    case 'list_models':return `${r.models.length} 个登记模型`;
    case 'route_model':return r.selected_model?'已匹配适用的识别方法':'已核对输入条件，尚未进行识别';
    case 'run_model':case 'recognize_emotion':return s.prediction_count?`已保存 ${s.prediction_count} 个窗口的识别结果`:'';
    case 'device_lookup':return `${(r.matches||[]).length} 个匹配设备`;
    case 'run_python':{const arts=(r.artifacts||[]).length,out=(r.stdout||'').split('\n').filter(Boolean).length;return [arts&&`${arts} 个产物`,out&&`stdout ${out} 行`,r.error?.message].filter(Boolean).join(' · ')||'已执行'}
    default:return '';
  }
}

/* ---------- manifests ---------- */
const SRC_LABEL={file_header:'文件头',file_content:'文件内容',dataset_adapter:'格式匹配',format_assumption:'格式假设',device_lookup:'设备库',user_confirmed:'用户确认',user_declared:'用户声明',inferred:'估计',unknown:'未确认'};
function srcTag(x){if(!x)return'';return`<span class="src ${esc(x.source)}">${esc(SRC_LABEL[x.source]||x.source)}</span>`}
function val(x){if(!x)return'—';const v=x.value;if(v===null||v===undefined)return'未知';if(typeof v==='object')return'映射表';return v}
const EVIDENCE_LABEL={data_shape:'数据形状',labels_shape:'标签形状',sample_rate_hz:'采样率',channel_names:'通道顺序',unit:'单位',reference:'参考',preprocessing:'处理流程',label_names:'标签含义',dataset:'数据集声明',origin:'文件来源'};
function metadataEvidence(m){
  let html='';
  if(m.format_profile)html+=`<div class="kv"><span class="k">格式候选</span><span class="v">${esc(({deap_preprocessed:'DEAP 预处理结构'})[m.format_profile]||m.format_profile)}</span></div>`;
  if(m.dataset&&m.dataset!=='unknown')html+=`<div class="kv"><span class="k">数据集登记</span><span class="v">${esc(m.dataset)}</span></div>`;
  if(m.format_profile||m.dataset&&m.dataset!=='unknown')html+=`<div class="kv"><span class="k">来源核验</span><span class="v">${m.metadata_evidence?.origin?.value==='verified'?'已核验':'未核验'}</span></div>`;
  if(m.metadata_evidence)html+=`<details class="metadata-evidence"><summary><span class="ms">chevron_right</span>元数据依据</summary><div class="evidence-rows">${Object.entries(m.metadata_evidence).filter(([key])=>key!=='origin').map(([key,entry])=>`<div class="evidence-row"><span>${esc(EVIDENCE_LABEL[key]||key)}</span><span>${esc(typeof entry.value==='object'?JSON.stringify(entry.value):entry.value)}${srcTag(entry)}</span></div>`).join('')}</div></details>`;
  return html;
}
function renderManifest(m,preview){
  const c=document.createElement('div');c.className='card record-card';
  const icon=m.modality==='eeg'?'monitor_heart':m.modality==='video'?'videocam':m.modality==='image'?'image':'description';
  let h=`<h4 class="title-s"><span class="ms sm">${icon}</span><span class="record-name" title="${esc(m.original_name||'')}">${esc(m.original_name||m.manifest_id)}</span><span class="src record-format">${esc((m.source_format||'').toUpperCase())}</span></h4>`,detail='';
  if(m.modality==='eeg'){
    const facts=[`${m.n_channels} 通道`];
    if(m.sample_rate_hz!=null)facts.push(`${m.sample_rate_hz} Hz`);
    if(m.trial_count>1)facts.push(`${m.trial_count} 个试次`);
    h+=`<div class="record-facts">${facts.map(fact=>`<span>${esc(fact)}</span>`).join('')}</div>`;
    const duration=m.duration_s==null?`${m.n_samples} 点`:`${m.duration_s} s`;
    if(m.signal_kind==='epochs'){
      const segments=m.segments_preview||m.segments,total=m.n_segments??segments.length;
      h+=`<div class="record-scope">${m.trial_count>1?`当前第 ${m.trial_index+1} 个试次${m.trial_selection==='default_first'?'（默认）':''} · `:''}${total} 个独立 Epoch · 累计采样 ${duration}</div>`;
      detail+=`<p class="record-detail-note">Epoch 之间不连续，累计采样量不代表连续记录时长。</p>`;
      if(m.sample_rate_hz!=null)detail+=`<div class="artifact-meta">坐标预览：${esc(segments.slice(0,4).map(segment=>coordinateLabel({epoch_index:segment.epoch_index,epoch_relative_time_range_s:[segment.epoch_tmin_s,segment.epoch_tmin_s+(segment.end_sample-segment.start_sample)/m.sample_rate_hz]})).join('；'))}</div>`;
    }else{
      h+=`<div class="record-scope">${m.trial_count>1?`当前第 ${m.trial_index+1} 个试次${m.trial_selection==='default_first'?'（默认）':''} · 该试次 `:''}${duration}</div>`;
    }
    const needed=[];
    if(m.sample_rate_hz==null)needed.push('采样率');
    if(!m.unit||m.unit.value==null||m.unit.value==='unknown')needed.push('物理单位');
    if(needed.length)h+=`<p class="record-notice">${needed.join('、')}待补充，涉及${needed.length>1?'时间和幅值':needed[0]==='采样率'?'时间':'幅值'}的分析时需要确认。</p>`;
    detail+=`<div class="kv"><span class="k">采样点</span><span class="v">${m.n_samples}</span><span class="k">单位</span><span class="v">${esc(val(m.unit))}${srcTag(m.unit)}</span><span class="k">布局</span><span class="v">${esc(val(m.channel_layout))}${srcTag(m.channel_layout)}</span><span class="k">参考</span><span class="v">${esc(({average:'平均参考',unknown:'未确认'})[m.reference]||m.reference||'未确认')}${srcTag(m.metadata_evidence?.reference)}</span>${m.trial_id?`<span class="k">试次标识</span><span class="v">${esc(m.trial_id)}</span>`:''}</div>`;
    detail+=metadataEvidence(m);
    detail+=`<details><summary><span class="ms">chevron_right</span>通道名 (${m.n_channels})</summary><pre class="raw">${esc((m.channel_names||[]).join(', '))}</pre></details>`;
    if(m.labels)detail+=`<details><summary><span class="ms">chevron_right</span>标签</summary><pre class="raw">${esc(JSON.stringify(m.labels,null,2))}</pre></details>`;
  }else if(m.modality==='video'){
    h+=`<div class="record-facts">${[m.duration_s!=null?m.duration_s+' s':null,m.width&&m.height?m.width+' × '+m.height:null,m.fps!=null?m.fps+' fps':null].filter(Boolean).map(fact=>`<span>${esc(fact)}</span>`).join('')}</div>`;
    detail+=`<div class="kv"><span class="k">用途</span><span class="v">${esc(({face:'面部记录',stimulus:'刺激内容',other:'其它'})[m.role?.value]||'待确认')}</span></div>`;
    if(m.sync)detail+=`<p class="record-detail-note">视频 0 秒对应原 EEG ${m.sync.start_offset_s} s · ${m.sync.source==='user_confirmed'?'已确认同步':'已记录同步声明'}</p>`;
  }else if(m.modality==='document'){
    h+=`<div class="record-facts"><span>${m.page_count||1} 页</span><span>${(m.size_bytes/1024).toFixed(0)} KB</span></div>`;
    if(m.document_index)detail+=`<div class="kv"><span class="k">检索</span><span class="v">${m.document_index.n_chunks} 个片段 · ${esc(({ready:'已解析',partial:'部分页面待 OCR',ocr_required:'需要 OCR',empty:'没有可检索正文'})[m.document_index.status])}</span></div>`;
  }else if(m.modality==='image'){
    h+=`<div class="record-facts"><span>${m.width} × ${m.height}</span><span>${(m.size_bytes/1024).toFixed(0)} KB</span></div>`;
  }
  if(m.compat&&m.compat.length){const status={compatible:'符合输入要求',mappable:'需要映射',incompatible:'不符合输入要求'};detail+=`<details><summary><span class="ms">chevron_right</span>识别方法与输入要求</summary><div class="record-methods">${m.compat.map(r=>`<div><strong>${esc(r.model_id)}</strong><span>${esc(status[r.status])}</span>${r.reasons?.length?`<p>${esc(r.reasons.join('；'))}</p>`:''}</div>`).join('')}</div></details>`}
  if(m.warnings?.length)detail+=`<details><summary><span class="ms">chevron_right</span>读取说明 (${m.warnings.length})</summary><ul class="record-reading-notes">${m.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></details>`;
  detail+=`<details class="record-raw"><summary><span class="ms">chevron_right</span>原始记录信息</summary><pre class="raw">${esc(JSON.stringify(m,null,2))}</pre></details>`;
  h+=`<div class="record-actions"><button class="chip small" type="button" data-open-attachment="${esc(m.manifest_id)}">预览${m.modality==='eeg'?'记录':m.modality==='video'?'视频':m.modality==='image'?'图片':'文档'}</button></div><details class="record-details"><summary><span class="ms">chevron_right</span>记录详情</summary><div class="record-detail-body">${detail}</div></details>`;
  if(m.modality==='eeg'&&m.quality&&Object.keys(m.quality).length)h+=`<details class="record-quality"><summary><span class="ms">chevron_right</span>质量初筛</summary><div class="record-detail-body">${qualitySummary(m.quality,m)}</div></details>`;
  c.innerHTML=h;return c;
}
function addManifest(m,preview){
  const i=manifests.findIndex(x=>x.m.attachment_sha256===m.attachment_sha256&&(x.m.trial_index??null)===(m.trial_index??null)); const entry={m,preview:preview||(i>=0&&manifests[i].m.manifest_id===m.manifest_id?manifests[i].preview:null)};
  if(i>=0)manifests[i]=entry; else manifests.push(entry);
  $('#noData').style.display='none';const box=$('#manifests');box.innerHTML='';manifests.forEach(x=>box.appendChild(renderManifest(x.m,x.preview)));badge('#bData',manifests.length);
}
/* multi-file formats (EEGLAB .set+.fdt, BrainVision .vhdr+.vmrk+.eeg): data files first, then the header, one at a time */
const SIDECAR=/\.(fdt|vmrk|eeg)$/i;
function restorePendingFiles(snapshot){
  const staged=snapshot.staged_manifests||[],ids=new Set(staged.map(m=>m.manifest_id));
  pendingFiles=pendingFiles.filter(file=>{const state=fileUploads.get(file);return !state.manifest||ids.has(state.manifest.manifest_id)});
  for(const manifest of staged){
    if(pendingFiles.some(file=>fileUploads.get(file).manifest?.manifest_id===manifest.manifest_id))continue;
    const file={name:manifest.original_name,size:manifest.size_bytes||0,lastModified:0};
    pendingFiles.push(file);fileUploads.set(file,{sessionId:snapshot.session_id,groupId:manifest.upload_group_id,status:'ready',manifest,preview:snapshot.previews?.[manifest.manifest_id],error:''});
  }
  pendingFilesBySession.set(snapshot.session_id,pendingFiles);
}
function discardUploadedFile(state){
  return state.manifest?api(`/sessions/${state.sessionId}/attachments/${state.manifest.manifest_id}`,{method:'DELETE'}):Promise.resolve();
}
function queueFiles(list){
  if(streaming||submitting||navigating){snack('请等待当前操作完成后添加文件');return}
  const files=[...list];
  const unfinished=pendingFiles.find(file=>{
    const state=fileUploads.get(file);
    return /\.(vhdr|ahdr|set|fdt|eeg|vmrk)$/i.test(file.name)&&!state.manifest&&!pendingFiles.some(other=>{
      const owner=fileUploads.get(other);return owner.groupId===state.groupId&&owner.manifest?.source_files?.some(path=>path.split(/[\\/]/).at(-1)===file.name);
    });
  });
  const groupId=unfinished?fileUploads.get(unfinished).groupId:'upl_'+crypto.randomUUID().replace(/-/g,'');
  for(const file of files)if(!pendingFiles.some(x=>x.name===file.name&&x.size===file.size&&x.lastModified===file.lastModified)){
    pendingFiles.push(file);
    if(!fileUploads.has(file)||fileUploads.get(file).sessionId!==sessionId)fileUploads.set(file,{sessionId,groupId,status:'queued',manifest:null,error:''});
  }
  renderPendingFiles();
  if(files.length){$('#input').focus();uploadPending().catch(reportError)}
}
function removePendingFile(file){
  const state=fileUploads.get(file),base=file.name.replace(/\.[^.]+$/,'').toLowerCase(),sameBase=pendingFiles.filter(item=>fileUploads.get(item).groupId===state.groupId&&item.name.replace(/\.[^.]+$/,'').toLowerCase()===base);
  const format=/\.(vhdr|vmrk|eeg)$/i.test(file.name)&&sameBase.some(item=>/\.vhdr$/i.test(item.name))?/\.(vhdr|vmrk|eeg)$/i:/\.(set|fdt)$/i.test(file.name)&&sameBase.some(item=>/\.set$/i.test(item.name))?/\.(set|fdt)$/i:null;
  const owner=pendingFiles.find(item=>{const candidate=fileUploads.get(item);return candidate.groupId===state.groupId&&candidate.manifest?.source_files?.some(path=>path.split(/[\\/]/).at(-1)===file.name)});
  const members=owner?new Set(fileUploads.get(owner).manifest.source_files.map(path=>path.split(/[\\/]/).at(-1))):null;
  const removed=members?pendingFiles.filter(item=>fileUploads.get(item).groupId===state.groupId&&members.has(item.name)):format?sameBase.filter(item=>format.test(item.name)):[file];
  for(let i=pendingFiles.length-1;i>=0;i--)if(removed.includes(pendingFiles[i]))pendingFiles.splice(i,1);
  for(const item of removed)discardUploadedFile(fileUploads.get(item)).catch(reportError);
  renderPendingFiles();if(removed.length>1)snack('已移除关联脑电文件组');
}
function renderPendingFiles(){
  const needsModel=hasImageInput()&&!modelCapabilities[currentLLMModel]?.image_input;
  const box=$('#pendingFiles');box.replaceChildren();box.hidden=!pendingFiles.length&&!needsModel;
  pendingFiles.forEach(file=>{
    const state=fileUploads.get(file),chip=document.createElement('span');chip.className='pending-file '+state.status;
    if(state.manifest?.modality==='image'){const image=document.createElement('img');image.className='pending-file-thumb';image.src=`/sessions/${state.sessionId}/attachments/${state.manifest.manifest_id}/file?view=1`;image.alt='';chip.appendChild(image)}
    const label=document.createElement('span');label.className='pending-file-name';label.textContent=file.name;label.title=file.name;
    if(state.manifest){label.classList.add('previewable');label.setAttribute('role','button');label.tabIndex=0;label.title='预览 '+file.name;label.onclick=()=>openAttachment(state.manifest.manifest_id);label.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openAttachment(state.manifest.manifest_id)}}}
    const status=document.createElement('span');status.className='pending-file-status';status.textContent=({queued:'等待上传',uploading:'上传中',checking:'检查中',ready:state.manifest?'已读入':'附属文件已保存',error:'失败'})[state.status];status.title=state.error;status.setAttribute('aria-live','polite');
    chip.append(label,status);
    if(state.status==='error'){
      const retry=document.createElement('button');retry.type='button';retry.className='pending-file-retry';retry.textContent='重试';retry.title=state.error;retry.disabled=uploading||submitting;
      retry.onclick=()=>{state.status='queued';state.error='';renderPendingFiles();uploadPending().catch(reportError)};chip.appendChild(retry);
    }
    const remove=document.createElement('button');remove.type='button';remove.title=`移除 ${file.name}`;remove.setAttribute('aria-label',remove.title);remove.textContent='×';
    remove.disabled=(submitting||streaming)&&!uploading;remove.onclick=()=>removePendingFile(file);chip.appendChild(remove);box.appendChild(chip);
  });
  if(needsModel){
    const model=llmModels.find(name=>modelCapabilities[name]?.image_input);
    const choose=document.createElement('button');choose.type='button';choose.className='chip small image-model-choice';choose.textContent=model?'用 '+model+' 看图':'选择看图模型';choose.disabled=streaming||submitting||uploading||changingModel;
    choose.onclick=()=>model?selectLLMModel(model).then(renderPendingFiles).catch(reportError):$('#llmSelector').click();box.appendChild(choose);
  }
  autosize();
}
function uploadPending(){
  if(uploadTask)return uploadTask;
  if(!pendingFiles.some(file=>fileUploads.get(file).status==='queued'))return Promise.resolve();
  const first=pendingFiles.find(file=>fileUploads.get(file).status==='queued');
  uploadTask=(async()=>{
    try{await ensureSession()}catch(error){const state=fileUploads.get(first);state.status='error';state.error=error.message;renderPendingFiles();throw error}
    const id=sessionId,version=viewVersion,files=pendingFiles;
    uploading=true;autosize();
    try{
      for(;;){
        const file=files.filter(item=>fileUploads.get(item).status==='queued').sort((a,b)=>(SIDECAR.test(b.name)?1:0)-(SIDECAR.test(a.name)?1:0))[0];
        if(!file)break;
        const state=fileUploads.get(file);state.sessionId=id;state.status='uploading';renderPendingFiles();
        $('#uploadProgress').textContent='准备本地文件 · '+file.name;$('#uploadProgress').hidden=false;
        try{
          const registered=await upload(file,id,state.groupId,()=>{state.status='checking';if(version===viewVersion)renderPendingFiles()});state.manifest=registered.manifest;state.preview=registered.preview;state.status='ready';
          if(registered.manifest?.modality==='image'&&!llmModels.length)await loadLLMModels();
          if(registered.manifest?.source_files){
            const names=new Set(registered.manifest.source_files.map(path=>path.split(/[\\/]/).at(-1))),header=files.find(item=>fileUploads.get(item).groupId===state.groupId&&item.name===registered.manifest.original_name);
            for(const member of files)if(fileUploads.get(member).groupId===state.groupId&&names.has(member.name)){
              const memberState=fileUploads.get(member);memberState.status='ready';memberState.error='';
              if(header){memberState.manifest=member===header?registered.manifest:null;memberState.preview=member===header?registered.preview:null}
            }
          }
          if(!files.includes(file))await discardUploadedFile(state);
        }catch(error){state.status='error';state.error=error.message;if(version===viewVersion)renderPendingFiles();throw error}
        if(version===viewVersion)renderPendingFiles();
      }
    }finally{
      uploading=false;
      if(version===viewVersion){$('#uploadProgress').hidden=true;renderPendingFiles();restoreStatus();refreshSessions().catch(reportError)}
    }
  })().finally(()=>{uploadTask=null;autosize()});
  autosize();
  return uploadTask;
}
function cleanSummary(s){
  if(s.method==='ica_iclabel'){const r=s.removed||[];return r.length?`剔除 ${r.length} 个成分：${r.map(x=>`IC${x.index} ${({brain:'脑源活动','muscle artifact':'肌电','eye blink':'眨眼','heart beat':'心搏','line noise':'工频干扰','channel noise':'通道噪声',other:'其他'})[x.label]||x.label} ${x.prob}`).join('，')}`:'ICLabel 未发现超过阈值的伪迹成分'}
  if(s.method==='asr')return `ASR 截止参数 ${s.cutoff}`+(s.changed_fraction!=null?` · ${((s.changed_fraction)*100).toFixed(1)}% 采样点发生重构变化`:s.relative_rms_change!=null?` · 相对 RMS 变化 ${(s.relative_rms_change*100).toFixed(1)}%`:'');
  if(s.method==='bad_channels'){const b=s.bad_channels||[];return b.length?`坏道 ${b.join(', ')} · ${({interpolate:'已插值',drop:'已删除',mark:'仅标记'})[s.action]||s.action}`:'未发现坏道'}
  return ''}
async function upload(file,id,groupId,onUploaded){
  const fd=new FormData();fd.append('file',file);fd.append('staged','true');fd.append('upload_group_id',groupId);
  setConn('busy','读取 '+file.name);const ev=traceEv('run','读取数据',file.name);
  let j;
  try{
    j=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST',`/sessions/${id}/attachments`);xhr.responseType='json';
      xhr.upload.onload=onUploaded;
      xhr.onload=()=>{const result=xhr.response;if(xhr.status>=200&&xhr.status<300)resolve(result);else reject(new Error(result?.worker?.error?.message||result?.error?.message||result?.error||`读取 ${file.name} 失败 (${xhr.status})`))};
      xhr.onerror=()=>reject(new Error(`上传 ${file.name} 失败，请检查连接后重试`));xhr.onabort=()=>reject(new Error(`上传 ${file.name} 已取消`));xhr.send(fd);
    });
  }catch(error){ev.className='ev err';ev.querySelector('span').textContent='失败';traceDetail(ev,'错误',error.message);throw error}
  if(j.sidecar){ev.className='ev ok';ev.querySelector('span').textContent='附属文件';traceDetail(ev,'说明',j);setConn('ok','已连接');snack(j.note||('已保存 '+file.name));return j}
  ev.className='ev ok';ev.querySelector('span').textContent='完成';traceDetail(ev,'数据详情',j.manifest);
  setConn('ok','已连接');return j;
}

/* ---------- artifacts ---------- */
function coordinateLabel(row){return row.epoch_index!=null?`Epoch ${row.epoch_index} · 事件相对 ${row.epoch_relative_time_range_s[0]}–${row.epoch_relative_time_range_s[1]} s`:`原记录 ${row.original_time_range_s[0]}–${row.original_time_range_s[1]} s`}
function coordinatePreview(ranges,total=ranges.length){const shown=ranges.slice(0,8),omitted=total>shown.length;return `坐标${omitted?'预览':''}：${shown.map(coordinateLabel).join('；')}${omitted?`（显示 ${shown.length} / ${total} 段）`:''}`}
function windowTimeLabel(row){
  if(row.epoch_index!=null)return coordinateLabel(row);
  if(row.original_time_ranges_s)return row.original_time_ranges_s.map(coordinateLabel).join('；');
  const range=row.original_recording_time_range_s||row.original_time_range_s;
  return range?`原记录 ${range[0]}–${range[1]} s`:`窗口 ${row.window_index??row.index??row.window_start}`;
}
function qualitySummary(q,m){
  const selected=q.scope==='selected_signal_range',channels=q.channels?q.channels.map(row=>row.channel):m.channel_names.filter((_,i)=>m.channel_types[i]==='eeg');
  const coordinates=q.sample_original_time_ranges_s||q.original_time_ranges_s;
  let h=`<div class="label-m muted" style="margin-top:8px">质量检查 · ${selected?'指定范围':'开头初筛'}</div><div class="artifact-meta">${channels.length} 个通道 · ${q.sample_count} 个样本</div>`;
  let scope=`<div class="quality-channels">${esc(channels.join(', '))}</div>`;
  if(coordinates)scope+=`<div class="artifact-meta">${esc(coordinatePreview(coordinates))}</div>`;
  else if(q.sample_time_range_s&&m.signal_kind!=='epochs')scope+=`<div class="artifact-meta">检查范围 ${q.sample_time_range_s[0]}–${q.sample_time_range_s[1]} s</div>`;
  h+=`<div class="kv"><span class="k">平坦通道</span><span class="v">${q.flat_channels.length?esc(q.flat_channels.join(', ')):'检查范围内未发现'}</span></div>`;
  if(q.line_noise_ratio!=null){
    const noiseCoordinates=q.line_noise_original_time_ranges_s||(selected?coordinates:null),bands=q.line_noise_bands_hz.map(([low,high])=>`${low}–${high} Hz`).join(' / ');
    h+=`<div class="kv"><span class="k">工频能量占比</span><span class="v">${(q.line_noise_ratio*100).toFixed(1)}% · ${esc(bands)}</span></div>`;
    if(noiseCoordinates)scope+=`<div class="artifact-meta">工频检查 ${esc(coordinatePreview(noiseCoordinates))}</div>`;
    else if(m.signal_kind!=='epochs')scope+=`<div class="artifact-meta">工频检查 ${q.line_noise_time_range_s[0]}–${q.line_noise_time_range_s[1]} s</div>`;
  }
  h+=`<details class="quality-scope"><summary><span class="ms">chevron_right</span>检查通道与范围</summary>${scope}</details>`;
  return h;
}
function bars(rel){return `<div class="bars">`+Object.entries(rel).map(([b,v])=>`<span>${b}</span><span class="bar"><i style="width:${Math.round((v||0)*100)}%;background:${BAND_COLOR[b]||'#888'}"></i></span><span class="v">${v==null?'—':(v*100).toFixed(1)+'%'}</span>`).join('')+`</div>`}
function featureSummary(s){let h='';
  if(s.relative_bandpower)h+=`<div class="label-m muted">相对频带功率（${s.n_windows} 窗 × ${s.n_channels} 通道平均）</div>`+bars(s.relative_bandpower);
  if(s.top_alpha_windows)h+=`<div class="label-m muted" style="margin-top:8px">α 功率最高的窗口</div><div class="tags">${s.top_alpha_windows.map(w=>`<span class="tag kind">${esc(windowTimeLabel(w))} · ${(w.alpha_power??w.alpha_uV2).toFixed(1)} ${esc(s.power_unit||'')}</span>`).join('')}</div>`;
  if(s.alpha_by_channel_top5)h+=`<div class="label-m muted" style="margin-top:8px">α 功率最高的通道</div><div class="tags">${s.alpha_by_channel_top5.map(c=>`<span class="tag kind">${esc(c.channel)} ${(c.alpha_power??c.alpha_uV2).toFixed(1)} ${esc(s.power_unit||'')}</span>`).join('')}</div>`;
  if(s.mean_de)h+=`<div class="kv" style="margin-top:8px"><span class="k">差分熵均值 ${esc(s.de_unit||'')}</span><span class="v">${Object.entries(s.mean_de).map(([b,v])=>b+' '+(v==null?'—':v.toFixed(2))).join(' · ')}</span></div>`;
  if(s.frontal_alpha_asymmetry)h+=`<div class="kv" style="margin-top:6px"><span class="k">α 不对称</span><span class="v">${Object.entries(s.frontal_alpha_asymmetry).map(([p,v])=>p+' '+(v==null?'—':v.toFixed(3))).join(' · ')}</span></div>`;
  if(s.asymmetry_note&&!s.frontal_alpha_asymmetry)h+=`<div class="warnline"><span class="ms">info</span><span>${esc(s.asymmetry_note)}</span></div>`;
  if(s.mean_hjorth){const units=s.feature_component_units?.hjorth||[];h+=`<div class="kv" style="margin-top:6px"><span class="k">Hjorth</span><span class="v">活动度 ${s.mean_hjorth.activity.toFixed(1)} ${esc(units[0]||'')} · 移动度 ${s.mean_hjorth.mobility.toFixed(3)} ${esc(units[1]==='per_sample'?'每采样点':units[1]||'')} · 复杂度 ${s.mean_hjorth.complexity.toFixed(3)}</span></div>`}
  for(const note of (s.notes||[]))h+=`<div class="warnline">${esc(note)}</div>`;
  return h||'<div class="muted body-s">特征数据已生成，可下载查看。</div>'}
function artifactTitle(a){
  const title=TranscriptUI.resultTitle(a);if(a.kind!=='normalized_signal')return title;
  const source=manifests.find(value=>(a.source_manifest_ids||[]).includes(value.m.manifest_id))?.m;
  const method=({asr:'ASR 清洗',ica_iclabel:'ICA 清洗'})[a.summary?.method]||'处理后 EEG';
  return source?`${method} · ${source.original_name}${source.trial_count>1?' · 试次 '+(source.trial_index+1):''}`:title;
}
function artifactTimeRange(a){return a.signal?.kind==='epochs'?null:a.summary?.original_recording_time_range_s||a.analysis?.original_recording_time_range_s||a.time_range_s}
function artifactTimeLabel(a){
  const analysis=a.analysis||{},ranges=analysis.original_time_ranges_s||analysis.original_time_ranges_s_preview;
  if(ranges?.length)return coordinatePreview(ranges,analysis.n_time_ranges??ranges.length);
  if(a.signal?.kind==='epochs')return '独立 Epoch · 各段不连续，按事件相对坐标解释';
  const range=artifactTimeRange(a);return range?`分析范围 ${range[0]}–${range[1]} s`:'';
}
function coverageSummary(coverage){
  let h=`<div class="artifact-meta">累计证据 ${coverage.covered_duration_s} s · ${coverage.n_windows} 个窗口 · ${coverage.complete?'覆盖完整':'有未覆盖范围'}</div>`;
  const ranges=coverage.original_time_ranges_s||coverage.original_time_ranges_s_preview;
  if(ranges)h+=`<div class="artifact-meta">${esc(coordinatePreview(ranges,coverage.n_time_ranges??ranges.length))}</div>`;
  return h;
}
function emotionSummary(s){
  const labels={LVLA:'低效价 · 低唤醒',LVHA:'低效价 · 高唤醒',HVLA:'高效价 · 低唤醒',HVHA:'高效价 · 高唤醒'};
  let h=`<div class="kv"><span class="k">结果类型</span><span class="v">${s.assessment_status==='hypothesis'?'特征候选':'测量审阅'}</span></div>`;
  if(s.assessment_status==='hypothesis')h+=`<div class="kv"><span class="k">候选标签</span><span class="v">${esc(labels[s.label]||s.label)}</span></div>`;
  h+=`<p class="body-s">${esc(s.rationale)}</p><div class="label-m muted">基于已测特征的记录</div>`;
  if(s.coverage)h+=coverageSummary(s.coverage);
  if(s.prediction_preview?.length)h+=`<div class="tags">${s.prediction_preview.map(row=>`<span class="tag kind">${esc(windowTimeLabel(row))} · ${row.assessment_status==='hypothesis'?'候选：'+esc(labels[row.label]||row.label):'情绪未评估'}</span>`).join('')}</div>`;
  return h;
}
function predictionSummary(s){
  let h=`<div class="kv"><span class="k">任务</span><span class="v">${esc(MODEL_TASK_LABEL[s.task]||s.task)}</span><span class="k">输出</span><span class="v">${s.output_kind==='classification'?'逐窗分类':'逐窗回归'} · ${s.prediction_count} 个结果</span></div>`;
  if(s.paired_video)h+=`<div class="artifact-meta">使用脑电与配对视频 · 视频0秒对应原EEG ${s.paired_video.sync_offset_s} s</div>`;
  if(s.output_kind==='classification'){
    const calibration={unknown:'校准状态未知',uncalibrated:'未经校准',calibrated:'已报告校准'}[s.calibration.status];
    h+=`<div class="artifact-meta">${s.score_kind==='probabilities'?'类别概率':'类别 logits'} · ${calibration}${s.calibration.method?' · '+esc(s.calibration.method):''}</div>`;
    if(s.calibration.reference)h+=`<div class="artifact-meta">校准依据：${esc(s.calibration.reference)}</div>`;
  }
  if(s.coverage)h+=coverageSummary(s.coverage);
  h+=`<div class="label-m muted">逐窗结果预览 · ${s.prediction_preview.length} / ${s.prediction_count}</div><div class="prediction-rows">${s.prediction_preview.map(row=>{
    const meaning=row.label_text||(s.label_definitions||[]).find(item=>item.label===row.label)?.label_text;
    const classification=s.output_kind==='classification';
    const head=classification?`${esc(row.label)}${meaning&&meaning!==row.label?'（'+esc(meaning)+'）':''}`:'';
    const magnitude=classification?Math.max(...s.labels.map((_,index)=>Math.abs(Number(row.scores[index]))),1e-9):1;
    const result=classification?`<div class="score-bars">${s.labels.map((label,index)=>{const score=Number(row.scores[index]);return `<span class="score-bar"><b>${esc(label)}</b><span class="track"><i style="width:${Math.max(2,Math.round(Math.abs(score)/magnitude*100))}%"></i></span><em>${score.toPrecision(4)}</em></span>`}).join('')}</div>`:s.dimensions.map((dimension,index)=>`${esc(dimension.name)} ${Number(row.values[index].toPrecision(4))}${dimension.unit?' '+esc(dimension.unit):''}`).join(' / ');
    return `<div class="prediction-row"><span class="artifact-meta">${esc(windowTimeLabel(row))}${row.video_time_range_s?' · 视频 '+row.video_time_range_s.join('–')+' s':''}</span>${head?`<div class="body-s">${head}</div>`:''}<div class="body-s">${result}</div></div>`;
  }).join('')}</div>`;
  if(s.aggregation)h+=`<div class="label-m muted">实际聚合</div><pre class="raw">${esc(JSON.stringify(s.aggregation,null,2))}</pre>`;
  return h;
}
function resetAttachmentView(){attachmentViewer.reset();$('#dataList').hidden=false}
function openAttachment(id){
  showTabAndOpen('data');$('#dataList').hidden=true;$('#panelTitle').textContent='文件预览';
  return attachmentViewer.open(id);
}
function openReference(reference){
  if(reference.manifest_id)return openAttachment(reference.manifest_id);
  return openAttachment({workspace:reference.workspace_id,path:reference.path,kind:reference.kind});
}
async function referenceSuggestions(query){
  const root=workspaceCatalog.find(workspace=>workspace.workspace_id===currentWorkspaceID)?.root.replace(/\\/g,'/').toLowerCase();
  const records=[...manifests.map(entry=>entry.m),...pendingFiles.map(file=>fileUploads.get(file).manifest).filter(Boolean)];
  const files=[...new Map(records.filter(m=>!root||!m.source_path?.replace(/\\/g,'/').toLowerCase().startsWith(root+'/')).map(m=>[m.manifest_id,m])).values()]
    .filter(m=>(m.original_name||'').toLowerCase().includes(query.toLowerCase())).map(m=>({manifest_id:m.manifest_id,name:m.original_name,kind:'file'}));
  const workspace=currentWorkspaceID?await api(`/workspaces/${currentWorkspaceID}/mentions?query=${encodeURIComponent(query)}`):{items:[]};
  return [...workspace.items,...files].slice(0,40);
}
function resultGroups(){
  const groups=[],seen=new Set();let request='';
  for(const row of transcript.getSnapshot().rows){
    if(row.type==='user'){request=row.text||row.attachments.map(file=>file.name).join('、');continue}
    const ids=row.artifacts.filter(id=>artifacts[id]&&!seen.has(id));ids.forEach(id=>seen.add(id));
    const deliveries=TranscriptUI.workspaceDeliveries(row),title=TranscriptUI.resultGroupTitle(row,id=>artifacts[id],id=>manifests.find(value=>value.m.manifest_id===id)?.m.original_name||'');
    if(ids.length||deliveries.length)groups.push({id:row.id,title,request,ids,deliveries});
  }
  const remaining=Object.keys(artifacts).filter(id=>!seen.has(id));
  if(remaining.length)groups.unshift({id:'earlier',title:'早期分析结果',ids:remaining,deliveries:[]});
  return groups.reverse();
}
function renderResultScope(groups){
  const menu=$('#resultScopeMenu'),trigger=$('#resultScope');
  const options=[{value:'latest',label:'最近一次分析'},{value:'all',label:'全部分析结果'},...groups.map(group=>({value:group.id,label:group.title}))];
  menu.replaceChildren();
  for(const option of options){
    const button=document.createElement('button');button.type='button';button.className='selection-option';button.value=option.value;button.tabIndex=-1;
    button.setAttribute('role','option');button.setAttribute('aria-selected',String(option.value===resultScope));
    button.innerHTML=`<span>${esc(option.label)}</span>${option.value===resultScope?'<span class="ms">check</span>':''}`;
    button.onclick=()=>{resultScope=option.value;menu.hidePopover();renderResultCatalog();$('.sheet-body').scrollTop=0;trigger.focus()};menu.appendChild(button);
  }
  trigger.value=resultScope;$('#resultScopeLabel').textContent=options.find(option=>option.value===resultScope)?.label||'最近一次分析';
}
function openResultScope(last=false){
  const menu=$('#resultScopeMenu'),trigger=$('#resultScope');menu.showPopover();
  const rect=trigger.getBoundingClientRect(),height=menu.offsetHeight;
  menu.style.width=rect.width+'px';menu.style.left=rect.left+'px';menu.style.top=(innerHeight-rect.bottom>=height+12?rect.bottom+6:rect.top-height-6)+'px';
  const options=[...menu.querySelectorAll('[role=option]')];
  (last?options.at(-1):menu.querySelector('[aria-selected=true]')||options[0]).focus();
}
function workspaceDeliveryFolder(files,total){
  const first=files[0];if(total>files.length)return {workspace_id:first.workspace_id,path:'.',kind:'directory',name:'工作区'};
  const parts=first.path.split('/').slice(0,-1);
  for(const file of files){const directory=file.path.split('/').slice(0,-1);while(parts.some((part,index)=>part!==directory[index]))parts.pop()}
  return {workspace_id:first.workspace_id,path:parts.join('/')||'.',kind:'directory',name:parts.at(-1)||'工作区'};
}
function openResults(turnId){
  resultScope=turnId||'latest';selectedArtifact=null;$('#resultDetail').hidden=true;$('#resultCatalog').hidden=false;
  $('#resultSearch').value='';showTab('art');openSheet();renderResultCatalog();$('.sheet-body').scrollTop=0;
}
function renderResultCatalog(){
  const groups=resultGroups(),active=resultScope==='latest'?groups[0]?.id:resultScope;
  renderResultScope(groups);
  const query=$('#resultSearch').value.trim().toLowerCase(),list=$('#resultList');list.replaceChildren();
  const scoped=resultScope==='all'?groups:groups.filter(group=>group.id===active);
  let count=0;
  function resultRow(item){
    const button=document.createElement('button');button.className='result-row';button.dataset.resultId=item.artifact_id;
    const description=TranscriptUI.resultDescription(item),source=(item.source_manifest_ids||[]).map(id=>{const m=manifests.find(value=>value.m.manifest_id===id)?.m;return m?m.original_name+(m.trial_count>1?' · 试次 '+(m.trial_index+1):''):''}).filter(Boolean).join('、');
    button.innerHTML=`<span class="ms">${KIND_ICON[item.kind]||'description'}</span><span class="result-row-copy"><strong>${esc(artifactTitle(item))}</strong>${description?`<span>${esc(description)}</span>`:''}<small>${esc([TranscriptUI.resultFormat(item),source,artifactTimeLabel(item)].filter(Boolean).join(' · '))}</small></span><span class="ms">chevron_right</span>`;
    button.onclick=()=>openArtifact(item.artifact_id);return button;
  }
  for(const group of scoped){
    const saved=new Set(group.deliveries.flatMap(delivery=>delivery.files.map(file=>file.artifact_id).filter(Boolean)));
    const items=TranscriptUI.resultItems(group.ids,id=>artifacts[id]).filter(item=>!saved.has(item.artifact_id)).filter(item=>!query||[artifactTitle(item),TranscriptUI.resultDescription(item),...(item.source_manifest_ids||[]).map(id=>manifests.find(value=>value.m.manifest_id===id)?.m.original_name||'')].join(' ').toLowerCase().includes(query));
    const deliveries=group.deliveries.filter(delivery=>!query||delivery.files.some(file=>file.path.toLowerCase().includes(query)));
    if(!items.length&&!deliveries.length)continue;count+=items.length+deliveries.length;
    const section=document.createElement('section');section.className='result-group';
    const heading=document.createElement('h3');heading.textContent=group.title;section.appendChild(heading);
    if(group.request){const request=document.createElement('p');request.className='result-request';request.textContent=group.request;section.appendChild(request)}
    const summary=document.createElement('p');summary.className='result-group-summary';summary.textContent=[items.length?TranscriptUI.resultSummary(items):'',deliveries.length?'工作区文件':''].filter(Boolean).join(' · ');section.appendChild(summary);
    for(const item of items.filter(value=>!TranscriptUI.isProcessResult(value)&&value.kind!=='normalized_signal'))section.appendChild(resultRow(item));
    const signals=items.filter(value=>value.kind==='normalized_signal');
    if(signals.length){const details=document.createElement('details');details.className='signal-results';details.open=signals.length<5||Boolean(query);const label=document.createElement('summary');label.textContent=`EEG 数据 · ${signals.length} 份`;details.appendChild(label);for(const item of signals)details.appendChild(resultRow(item));section.appendChild(details)}
    const process=items.filter(TranscriptUI.isProcessResult);
    if(process.length){const details=document.createElement('details');details.className='process-results';details.open=Boolean(query);const label=document.createElement('summary');label.textContent=`过程数据 · ${process.length}`;details.appendChild(label);for(const item of process)details.appendChild(resultRow(item));section.appendChild(details)}
    for(const delivery of deliveries){
      const folder=workspaceDeliveryFolder(delivery.files,delivery.count),partial=delivery.count>delivery.files.length;
      const details=document.createElement('details');details.className='workspace-delivery';details.open=Boolean(query)||delivery.files.length<=6;
      const label=document.createElement('summary');label.textContent=`工作区文件 · ${delivery.count} 个`;
      details.appendChild(label);
      const directory=document.createElement('button');directory.className='result-directory';directory.textContent=folder.path==='.'?'打开工作区':'打开 '+folder.path;directory.onclick=()=>openReference(folder);details.appendChild(directory);
      if(partial){const note=document.createElement('p');note.className='result-history-note';note.textContent=`历史路径记录 ${delivery.files.length} / ${delivery.count}`;details.appendChild(note)}
      for(const file of delivery.files.filter(file=>!query||file.path.toLowerCase().includes(query))){
        const row=document.createElement('div');row.className='saved-file-row';
        const button=document.createElement('button');button.className='saved-file-open';button.dataset.workspaceFile=file.path;
        button.innerHTML=`<span class="ms">description</span><span class="result-row-copy"><strong>${esc(file.name||file.path.split('/').at(-1))}</strong><small>${esc(file.path)}</small></span>`;
        button.onclick=()=>file.artifact_id&&artifacts[file.artifact_id]?openArtifact(file.artifact_id):openReference(file);
        const download=document.createElement('a');download.className='icon-btn';download.href=`/workspaces/${encodeURIComponent(file.workspace_id)}/file?path=${encodeURIComponent(file.path)}&download=1`;download.download=file.name||file.path.split('/').at(-1);download.title='下载 '+download.download;download.setAttribute('aria-label',download.title);download.innerHTML='<span class="ms">download</span>';
        row.append(button,download);details.appendChild(row);
      }
      section.appendChild(details);
    }
    list.appendChild(section);
  }
  $('#noArt').style.display=groups.length?'none':'';$('#resultCatalog').hidden=Boolean(selectedArtifact)||!groups.length;
  if(!count&&groups.length){const empty=document.createElement('p');empty.className='result-empty';empty.textContent=query?'没有匹配的结果':'本轮还没有生成结果';list.appendChild(empty)}
}
function openArtifact(id){
  if(resultScope!=='all'){const groups=resultGroups(),current=resultScope==='latest'?groups[0]:groups.find(group=>group.id===resultScope);if(!current?.ids.includes(id)){const owner=groups.find(group=>group.ids.includes(id));if(owner)resultScope=owner.id}}
  selectedArtifact=id;showTab('art');openSheet();$('#resultCatalog').hidden=true;$('#resultDetail').hidden=false;
  renderArtifactDetail(artifacts[id]);$('.sheet-body').scrollTop=0;
  const report=$('#artifacts .report-preview');if(report)report.open=true;
  if(artifacts[id].summary?.details_omitted)api(`/artifacts/${id}`).then(record=>{addArtifactCard(record,true);if(selectedArtifact===id){const report=$('#artifacts .report-preview');if(report)report.open=true}}).catch(reportError);
}
$('#resultSearch').oninput=()=>renderResultCatalog();
$('#resultScope').onclick=()=>{const menu=$('#resultScopeMenu');if(menu.matches(':popover-open'))menu.hidePopover();else openResultScope()};
$('#resultScope').onkeydown=event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();openResultScope(event.key==='ArrowUp')}};
$('#resultScopeMenu').addEventListener('toggle',()=>$('#resultScope').setAttribute('aria-expanded',String($('#resultScopeMenu').matches(':popover-open'))));
$('#resultScopeMenu').addEventListener('keydown',event=>{
  const menu=$('#resultScopeMenu'),options=[...menu.querySelectorAll('[role=option]')],index=options.indexOf(document.activeElement);
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();options[event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length].focus()}
  else if(event.key==='Escape'){event.preventDefault();menu.hidePopover();$('#resultScope').focus()}
  else if(event.key==='Tab')menu.hidePopover();
});
$('#resultBack').onclick=()=>{selectedArtifact=null;$('#resultDetail').hidden=true;$('#resultCatalog').hidden=false;renderResultCatalog();$('.sheet-body').scrollTop=0};
function qualityBoard(summary){
  const rows=summary.preview||[];
  return `<section class="quality-board"><div class="quality-metrics"><span><b>${summary.file_count}</b>文件</span><span><b>${summary.measured}</b>已检查试次</span><span><b>${summary.not_measured}</b>未完成检查</span></div><div class="artifact-meta">完整记录 · 全部试次 · 遵守工作区排除清单</div><div class="quality-table"><table><thead><tr><th>文件 / 试次</th><th>范围</th><th>平坦通道</th><th>工频占比</th></tr></thead><tbody>${rows.map(row=>`<tr><td title="${esc(row.file)}">${esc(row.file)}${row.trial!=null?`<small>试次 ${row.trial+1} · ${row.channels} 通道</small>`:''}</td><td>${row.status==='measured'?row.start_s+'–'+row.end_s+' s':esc(row.error)}</td><td>${row.flat_channels?esc(row.flat_channels):row.status==='measured'?'未发现':'—'}</td><td>${row.line_noise_ratio!=null?(row.line_noise_ratio*100).toPrecision(3)+'%':'—'}</td></tr>`).join('')}</tbody></table></div><p class="artifact-meta">预览 ${rows.length} / ${summary.rows} 个试次，完整数值见 CSV。</p>${summary.files_with_flat_channels?.length?`<div class="quality-findings"><strong>有平坦通道的记录</strong>${summary.files_with_flat_channels.slice(0,8).map(file=>`<span>${esc(file)}</span>`).join('')}</div>`:''}</section>`;
}
function artifactSourceOverview(record){
  const sources=(record.source_manifest_ids||[]).map(id=>manifests.find(item=>item.m.manifest_id===id)?.m).filter(Boolean);
  const batchFiles=[...new Set((record.summary?.preview||[]).map(row=>row.file).filter(Boolean))];
  const inputNames=batchFiles.length?batchFiles:sources.map(source=>source.original_name||source.source_path.split(/[\\/]/).at(-1));
  const title=(record.summary||{}).workspace_id?workspaceCatalog.find(item=>item.workspace_id===record.summary.workspace_id)?.name:'';
  const method=(record.provenance?.created_by||record.created_by||'').split('@')[0];
  return `<div class="artifact-overview">${title?`<span><b>工作区</b>${esc(title)}</span>`:''}${inputNames.length?`<span><b>输入</b>${inputNames.slice(0,3).map(esc).join('、')}</span>`:''}${method?`<span><b>方法</b>${esc(TOOL_LABEL[method]||({workspace_quality:'完整记录质量检查',model_input_preparation:'模型输入准备'})[method]||method)}</span>`:''}</div>`;
}
function addArtifactCard(a,replace=false){
  if(artifacts[a.artifact_id]&&!replace)return;artifacts[a.artifact_id]=a;badge('#bArt',Object.keys(artifacts).length);
  if(selectedArtifact===a.artifact_id)renderArtifactDetail(a);
  transcript.metadata();
}
function renderArtifactDetail(a){
  $('#artifacts').replaceChildren();
  const c=document.createElement('div');c.className='card artcard';c.id='artifact-'+a.artifact_id;
  const markdownReport=a.kind==='report'&&(!a.media_type||a.media_type.startsWith('text/markdown'));
  let h=`<h4 class="title-s"><span class="ms sm" style="color:var(--md-primary)">${KIND_ICON[a.kind]||'inventory_2'}</span>${esc(artifactTitle(a))}</h4>`;
  const description=TranscriptUI.resultDescription(a);if(description)h+=`<p class="artifact-description">${esc(description)}</p>`;
  h+=artifactSourceOverview(a);
  if(a.summary?.file_count!=null&&Array.isArray(a.summary.preview))h+=qualityBoard(a.summary);
  if(artifactTimeLabel(a))h+=`<div class="artifact-meta">${esc(artifactTimeLabel(a))}</div>`;
  if(a.kind==='features')h+=`<div style="margin-top:8px">${featureSummary(a.summary||{})}</div>`;
  if(a.kind==='emotion_assessment')h+=`<div style="margin-top:8px">${emotionSummary(a.summary)}</div>`;
  if(a.kind==='prediction'&&a.summary?.method==='model_inference')h+=`<div class="prediction-card" style="margin-top:8px">${predictionSummary(a.summary)}</div>`;
  if(a.kind==='table'||a.kind==='report')h+=`<div style="margin-top:8px"><a class="dl" href="/artifacts/${esc(a.artifact_id)}/file${a.kind==='report'?'?download=1':''}" target="_blank"><span class="ms sm">download</span>下载 ${esc((a.summary?.format||(markdownReport?'md':'文件')).toUpperCase())}</a></div>`;
  if(['normalized_signal','windows','features','aggregate','prediction','emotion_assessment'].includes(a.kind))h+=`<div class="artifact-download"><a class="dl" href="/artifacts/${esc(a.artifact_id)}/file" download><span class="ms sm">download</span>下载${esc(artifactTitle(a))}</a></div>`;
  if(a.summary?.format==='signal_edf'&&a.representations?.json)h+=`<a class="chip small" href="/artifacts/${esc(a.artifact_id)}/file?format=json&download=1" download>采样范围与元数据 JSON</a>`;
  if(markdownReport)h+=`<details class="report-preview"><summary><span class="ms">chevron_right</span>阅读报告</summary><div class="md report-text">加载中…</div></details>`;
  if(a.kind==='plot'){
    h+=`<div class="thumb"><img src="/artifacts/${esc(a.artifact_id)}/file" alt="${esc(artifactTitle(a))}" onclick="lightbox(this.src)"></div>`;
    if(a.summary?.available_formats)h+=`<div class="plot-downloads"><span>下载</span>${a.summary.available_formats.map(format=>`<a href="/artifacts/${esc(a.artifact_id)}/file?format=${esc(format)}&download=1" download>${esc(format.toUpperCase())}</a>`).join('')}</div>`;
  }
  h+=`<details class="artifact-tech"><summary><span class="ms">chevron_right</span>来源与处理过程</summary>${a.kind==='prediction'&&a.summary?.method==='model_inference'?`<div class="artifact-meta">模型：${esc(a.summary.model_id)} · 版本：${esc(a.summary.model_version)}</div>`:''}<pre class="raw">展开读取实际产物记录</pre></details>`;
  c.innerHTML=h;const technical=c.querySelector('.artifact-tech');technical.addEventListener('toggle',async()=>{
    if(!technical.open||technical.dataset.loaded)return;
    const detail=technical.querySelector('pre');detail.textContent='正在读取实际产物记录…';
    try{const record=await api(`/artifacts/${a.artifact_id}`);detail.textContent=JSON.stringify(record,null,2);technical.dataset.loaded='true'}
    catch(error){detail.textContent=error.message}
  });
  const report=c.querySelector('.report-preview');if(report)report.addEventListener('toggle',async()=>{if(!report.open||report.dataset.loaded)return;try{const response=await fetch(`/artifacts/${a.artifact_id}/file`);if(!response.ok)throw new Error('报告文件无法读取');report.querySelector('.report-text').innerHTML=md(await response.text(),{images:true});report.dataset.loaded='true'}catch(error){report.querySelector('.report-text').textContent=error.message}});$('#artifacts').appendChild(c);
}
document.addEventListener('click',e=>{
  const executionArtifact=e.target.closest('[data-execution-artifact]');if(executionArtifact){openArtifact(executionArtifact.dataset.executionArtifact);return}
  const executionFile=e.target.closest('[data-execution-workspace]');if(executionFile){openReference({workspace_id:executionFile.dataset.executionWorkspace,path:executionFile.dataset.executionPath,kind:executionFile.dataset.executionKind});return}
  const attachment=e.target.closest('[data-open-attachment]');if(attachment){openAttachment(attachment.dataset.openAttachment);return}
  if(e.target.closest('[data-attachment-back]')){if(!attachmentViewer.back())showTab('data');return}
  if(e.target.matches('[data-preview-zoom]')){lightbox(e.target.src);return}
  const source=e.target.closest('[data-open-source]');if(source){$('#sourcePopover').hidePopover();focusKnowledge(source.dataset.openSource);return}
  if(e.target.closest('[data-source-back]')){$('#sourceDetail').hidden=true;$('#knowledge').hidden=false;return}
  const citation=e.target.closest('[data-know]');if(citation){e.preventDefault();sourcePreview(citation.dataset.know,citation);return}
  const artifact=e.target.closest('a.art');if(artifact){e.preventDefault();openArtifact(artifact.dataset.art)}
});

/* ---------- knowledge ---------- */
let knowledge={},facts={};
function addFact(f){
  if(!f||!(f.fact_id||f.text))return;const id=f.fact_id||f.text;if(facts[id])return;facts[id]=f;
  $('#noKnow').style.display='none';badge('#bKnow',Object.keys(knowledge).length+Object.keys(facts).length);
  let box=$('#facts');if(!box.querySelector('.factcard')){const c=document.createElement('div');c.className='card factcard';c.innerHTML=`<h4 class="title-s"><span class="ms sm" style="color:var(--md-primary)">hub</span>图谱关系</h4><div class="factlist"></div>`;box.appendChild(c)}
  const row=document.createElement('div');row.className='fact'+(f.status==='proposed'?' proposed':'');
  const ev=(f.evidence||[]).map(e=>`<button class="fact-source" data-know="${esc(e)}">${esc(knowledge[e]?SourceCitations.title(knowledge[e]):'摘录尚未读取')}</button>`).join(' ');
  row.innerHTML=`<span class="ms xs">${f.rel==='contradicts'?'warning':'arrow_right_alt'}</span><span class="ftext">${esc(f.text||'')}</span><span class="fev">${ev}</span>${f.status==='proposed'?'<span class="src unknown">未审核</span>':''}`;
  box.querySelector('.factlist').appendChild(row);
}
function addKnowledge(c,how){
  if(!c||!c.chunk_id)return;knowledge[c.chunk_id]??={...c,how};c=knowledge[c.chunk_id];
  $('#noKnow').style.display='none';badge('#bKnow',Object.keys(knowledge).length+Object.keys(facts).length);
  let card=document.getElementById('know-'+c.chunk_id);
  if(!card){card=document.createElement('div');card.className='card kcard';card.id='know-'+c.chunk_id;$('#knowledge').prepend(card)}
  card.className='source-row';
  card.innerHTML=`<button data-know="${esc(c.chunk_id)}"><span class="source-row-icon ms">${c.doc_type==='paper'?'article':'description'}</span><span class="source-row-copy"><strong>${esc(SourceCitations.title(c))}</strong><small>${esc(SourceCitations.typeLabel(c))} · ${esc(SourceCitations.location(c))}</small><span class="source-row-excerpt">${esc(SourceCitations.excerpt(c))}</span></span><span class="ms">chevron_right</span></button>`;transcript.metadata();
}
function sourceLinks(source){
  const original=SourceCitations.original(source,sessionId),publication=SourceCitations.publication(source);
  return (original?`<a href="${esc(original.url)}" target="_blank" rel="noopener">${esc(original.label)}<span class="ms">open_in_new</span></a>`:'')+(publication?`<a href="${esc(publication)}" target="_blank" rel="noopener">参考文献页面<span class="ms">open_in_new</span></a>`:'');
}
function sourceExcerptHTML(source){
  if(SourceCitations.kind(source)==='user_upload'&&/\.pdf$/i.test(SourceCitations.title(source)))return SourceCitations.excerpt(source).split(/\n\s*\n/).map(paragraph=>`<p>${esc(paragraph.replace(/\s+/g,' '))}</p>`).join('');
  const sectionKey=text=>text.replace(/[^\p{L}\p{N}]/gu,'').toLowerCase();
  const text=source.text.replace(/\[([^\]]+)\]\((?:\.\.\/)+(?:papers|references)\/([\w-]+)\.md#([^\)]+)\)/g,(_,label,doc,section)=>{
    const original=Object.values(knowledge).find(item=>item.doc_id===doc&&item.supports_notes?.includes(source.chunk_id)&&sectionKey(item.section)===sectionKey(decodeURIComponent(section).split(':~:text=')[0]));
    return original?`${label} [${original.chunk_id}]`:`${label}（原文片段未随本次检索提供）`;
  });
  return md(text);
}
function sourcePreview(id,anchor){
  const source=knowledge[id];if(!source){snack('这条历史引用没有保留可用的原文摘录');return}
  const popup=$('#sourcePopover');
  popup._sourceAnchor=anchor;anchor.setAttribute('aria-expanded','true');
  popup.innerHTML=`<div class="source-kicker">${esc(SourceCitations.typeLabel(source))} · ${esc(SourceCitations.location(source))}</div><h3>${esc(SourceCitations.title(source))}</h3><blockquote>${sourceExcerptHTML(source)}</blockquote><div class="source-actions"><button data-open-source="${esc(id)}" autofocus>查看完整摘录<span class="ms">arrow_right_alt</span></button>${sourceLinks(source)}</div>`;
  popup.showPopover();const rect=anchor.getBoundingClientRect();
  popup.style.left=Math.max(12,Math.min(rect.left,innerWidth-popup.offsetWidth-12))+'px';
  popup.style.top=(rect.bottom+popup.offsetHeight+12<innerHeight?rect.bottom+8:Math.max(12,rect.top-popup.offsetHeight-8))+'px';
}
$('#sourcePopover').addEventListener('toggle',event=>{if(event.newState==='closed')$('#sourcePopover')._sourceAnchor?.setAttribute('aria-expanded','false')});
function focusKnowledge(id){
  const source=knowledge[id];showTabAndOpen('know');const detail=$('#sourceDetail');detail.hidden=false;$('#knowledge').hidden=true;
  const note=SourceCitations.kind(source)==='project_note'?'项目知识笔记 · 引用与原文入口见下方。':'引用原文与定位信息';
  detail.innerHTML=`<button class="icon-btn source-back" data-source-back aria-label="返回来源列表" title="返回来源列表"><span class="ms">chevron_left</span></button><div class="source-kicker">${esc(SourceCitations.typeLabel(source))} · ${esc(SourceCitations.location(source))}</div><h3>${esc(SourceCitations.title(source))}</h3><div class="source-actions">${sourceLinks(source)}</div><p class="source-context-note">${note}</p><div class="source-excerpt md">${sourceExcerptHTML(source)}</div>${source.source?`<div class="source-bibliography"><span>${SourceCitations.kind(source)==='project_note'?'参考书目':'文档来源'}</span><p>${esc(source.source)}</p></div>`:''}${source.version?`<div class="source-version">资料版本 ${esc(source.version)}</div>`:''}`;
  $('.sheet-body').scrollTop=0;
}
async function stopGeneration(){
  stopRequested=true;autosize();setConn('busy','正在停止');
  try{await api(`/sessions/${sessionId}/stop`,{method:'POST'})}
  catch(error){stopRequested=false;autosize();if(streaming)setConn('busy','正在分析');throw error}
}
async function send(options={}){
  if(changingModel)return;
  if(!options.retry&&$('#input').value.trim().startsWith('/')){await runSlashCommand($('#input').value.trim());return}
  const retry=options.retry===true,answered=options.answers?pendingInput:null;
  const composerValue=$('#input').value,draft=retry?'':answered?inputAnswerText(answered,options.answers):composerValue.trim(),selected=[...pendingFiles],references=retry||answered?[]:fileMentions.references();
  if((!retry&&!draft&&!selected.length&&!references.length)||submitting||(navigating&&!uploadTask))return;
  if(streaming&&selected.length){snack('本轮结束后再发送文件');return}
  if(retry&&streaming){snack('当前请求仍在运行');return}
  await ensureSession();if(submitting)return;submitting=true;autosize();
  const version=viewVersion,id=sessionId,wasStreaming=streaming;let ownsStream=false;
  try{
    if(selected.length)await uploadPending();if(version!==viewVersion)return;
    const submitted=selected.filter(file=>pendingFiles.includes(file)),failed=submitted.find(file=>fileUploads.get(file).status==='error');
    if(failed)throw new Error(`${failed.name} 上传失败，请点击附件上的重试：${fileUploads.get(failed).error}`);
    if(!retry&&!draft&&!submitted.length&&!references.length)return;
    const attached=submitted.map(file=>fileUploads.get(file).manifest).filter(Boolean);
    if(!await ensureImageModel(attached))return;
    if(submitted.length&&!attached.length)throw new Error('附属文件已保存；请添加对应的头文件，组成可读取的脑电记录后再发送。');
    const imagesOnly=attached.length>0&&attached.every(manifest=>manifest.modality==='image');
    const text=retry?'':draft||(imagesOnly?'请解释本次上传的图片，结合图中的标题、坐标、图例和关键变化回答。':references.length?'请检查本次引用的数据，概述结构和质量并建议下一步分析。':'请检查本次上传的数据，概述结构和质量并建议下一步分析。');
    const view={text:answered?answered.questions.map(q=>options.answers[q.id]).join('\n'):draft||(imagesOnly?'解释上传的图片':references.length?'分析引用的数据':'分析上传的数据'),attachments:attached.filter(m=>!references.some(ref=>ref.manifest_id===m.manifest_id)).map(m=>({id:m.manifest_id,name:m.original_name,modality:m.modality})),references,questions:answered?answered.questions.map(q=>q.question):[]};
    if(!wasStreaming){requestFailure='';streamController=new AbortController();streaming=true;ownsStream=true;autosize();setConn('busy','正在分析')}
    const response=await fetch(`/sessions/${id}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,mode,retry,attachments:[...new Set([...attached.map(m=>m.manifest_id),...references.map(ref=>ref.manifest_id).filter(Boolean)])],references,display_text:view.text,...(answered?{input_request_id:answered.id,answers:options.answers}:{})}),signal:wasStreaming?undefined:streamController.signal});
    if(version!==viewVersion)return;
    if(!response.ok){const error=await response.json();throw new Error(error.error||`请求失败 (${response.status})`)}
    if(!retry){renderInputRequest(null);if($('#input').value===composerValue)$('#input').value='';fileMentions.clear(references);transcript.addUser(view);for(const file of submitted){const state=fileUploads.get(file);if(state.manifest)addManifest(state.manifest,state.preview)}pendingFiles=pendingFiles.filter(file=>!submitted.includes(file));renderPendingFiles();if(!currentTitle){currentTitle=(draft||submitted[0]?.name||references[0]?.name||'新分析').slice(0,40);$('#sessionLabel').textContent=currentTitle}}
    followBottom=true;submitting=false;autosize();
    if(response.status===202){snack('补充说明已送达');if(!wasStreaming){const snapshot=await api('/sessions/'+id);if(version!==viewVersion)return;renderSession(snapshot);watchSession(id,version)}return}
    liveCursor=null;eventTraces.clear();ownsStream=true;transcript.addTurn();
    await consumeEventStream(response,version);
  }catch(error){
    if(version!==viewVersion)return;requestFailure=error.message;
    if(ownsStream)transcript.finish(error.message);else snack(error.message);
  }finally{
    if(version===viewVersion){submitting=false;if(ownsStream){streaming=false;streamController=null;restoreStatus()}renderPendingFiles();refreshSessions().catch(reportError)}
  }
}

const SKILL_NAMES={general_eeg_analysis:'通用脑电分析',data_understanding:'数据理解',preprocess:'预处理',spectral_analysis:'频谱分析',feature_extraction:'特征提取',artifact_quality:'信号质量',emotion_recognition:'情绪研究',sleep_analysis:'睡眠分析',compare_conditions:'条件比较',report:'研究报告',scripting:'Python 分析脚本',eeg_toolboxes:'EEG 工具箱'};
const COMMANDS=[{name:'new',description:'开始新分析'},{name:'auto',description:'恢复自动选择分析方法'},{name:'skills',description:'查看分析方法与能力范围'},{name:'context',description:'查看当前上下文用量'},{name:'compact',description:'整理模型上下文，保留完整记录'},{name:'clone',description:'复制当前分析，比较另一种方法'},{name:'rename',description:'重命名当前会话'}];
function setActiveSkill(name){activeSkillName=name;const label=name?(SKILL_NAMES[name]||name):'自动选择方法';$('#activeSkill').textContent=label}

/* ---------- recognition models ---------- */
const MODEL_TASK_LABEL={emotion:'情绪识别',sleep_stage:'睡眠分期',embedding:'EEG 表征'};
const MODEL_STATE_LABEL={ready:'已接入',weights_pending:'待接入权重',adapter_pending:'待接入执行器',research_only:'研究参考'};
function modelSpecs(model){
  const input=model.input,modality=model.modalities.includes('face_video')?'EEG ＋ 面部视频':'EEG';
  return [modality,input.channels&&`${input.channels} 通道`,input.sampling_rate_hz&&`${input.sampling_rate_hz} Hz`,input.window_s&&`${input.window_s} 秒窗口`,input.feature&&'差分熵特征'].filter(Boolean);
}
function renderModels(){
  const box=$('#modelList'),list=modelCatalog.filter(m=>m.task===modelTask);box.replaceChildren();
  if(!list.length){box.innerHTML='<div class="card outlined muted body-m">该任务还没有登记模型。</div>';return}
  for(const model of list){
    const card=document.createElement('article');card.className='model-card';
    card.innerHTML=`<div class="model-card-head"><strong>${esc(model.display_name)}</strong><span class="model-state ${esc(model.availability)}">${esc(MODEL_STATE_LABEL[model.availability])}</span></div><p>${esc(model.dataset?model.dataset+' 数据配置':model.input_contract_status==='pending'?'自有训练模型':'自有模型接入配置')} · ${esc(MODEL_TASK_LABEL[model.task])}</p><div class="model-specs">${modelSpecs(model).map(s=>`<span>${esc(s)}</span>`).join('')}</div><details><summary><span class="ms">chevron_right</span>输入要求与接入详情</summary><p>${esc(model.blockers.length?model.blockers.join('；'):'权重与执行器已登记，运行时检查服务状态')}</p><p>输出标签：${esc(model.label_map.length?model.label_map.join(' / '):'按训练时的标签顺序填写')}</p>${model.input.sync_required?'<p>需要同步面部视频；刺激视频不能作为人脸输入。</p>':''}${model.split_protocol?`<p>评测协议：${esc(model.split_protocol)}</p>`:''}${model.limitations?.length?`<p>已知限制：${esc(model.limitations.slice(0,3).join('；'))}</p>`:''}<p class="model-id">${esc(model.model_id)}</p></details>`;
    const button=document.createElement('button');button.className='model-question';button.innerHTML='<span>核对当前数据是否适用</span><span class="ms">arrow_right_alt</span>';
    button.onclick=()=>{$('#input').value=`${model.display_name} 能用于当前数据吗？请核对输入要求与接入状态。`;if(narrow())closeSheet();autosize();$('#input').focus()};card.appendChild(button);box.appendChild(card);
  }
}
function updateCommands(){
  const value=$('#input').value,show=value.startsWith('/')&&!/\s/.test(value)&&!streaming;
  const menu=$('#commandMenu');menu.hidden=!show;$('#input').setAttribute('aria-expanded',String(show));
  if(!show){$('#input').removeAttribute('aria-activedescendant');return}
  const query=value.slice(1).toLowerCase();
  commandMatches=[...skillCatalog.map(s=>({name:s.name,description:SKILL_NAMES[s.name]||s.description,skill:true})),...COMMANDS].filter(c=>c.name.includes(query)||c.description.includes(query));
  commandIndex=Math.min(commandIndex,Math.max(0,commandMatches.length-1));menu.replaceChildren();
  commandMatches.forEach((c,index)=>{const button=document.createElement('button');button.type='button';button.className='command-option';button.id='command-'+index;button.setAttribute('role','option');button.setAttribute('aria-selected',String(index===commandIndex));button.innerHTML=`<b>${esc(c.description)}</b><span>${c.skill?'分析方法':'/'+esc(c.name)}</span>`;button.onmousedown=e=>e.preventDefault();button.onclick=()=>chooseCommand(index).catch(reportError);menu.appendChild(button)});
  if(!commandMatches.length){menu.textContent='没有匹配的命令或分析方法';$('#input').removeAttribute('aria-activedescendant')}else $('#input').setAttribute('aria-activedescendant','command-'+commandIndex);
}
function handleCommandKey(e){
  if($('#commandMenu').hidden||e.isComposing)return false;
  if(e.key==='Escape'){e.preventDefault();$('#commandMenu').hidden=true;$('#input').setAttribute('aria-expanded','false');return true}
  if((e.key==='ArrowDown'||e.key==='ArrowUp')&&commandMatches.length){e.preventDefault();commandIndex=(commandIndex+(e.key==='ArrowDown'?1:-1)+commandMatches.length)%commandMatches.length;updateCommands();document.getElementById('command-'+commandIndex).scrollIntoView({block:'nearest'});return true}
  if((e.key==='Enter'&&!e.shiftKey||e.key==='Tab')&&commandMatches.length){e.preventDefault();chooseCommand(commandIndex).catch(reportError);return true}
  return false;
}
async function chooseCommand(index){const command=commandMatches[index];await runSlashCommand('/'+command.name)}
async function activateSkill(name){
  await ensureSession();const id=sessionId,version=viewVersion;
  const result=await api(`/sessions/${id}/skill`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
  if(version!==viewVersion)return false;
  setActiveSkill(result.active_skill);snack(name?'已激活 '+(SKILL_NAMES[name]||name):'已恢复自动选择');return true;
}
async function runSlashCommand(value){
  if(streaming||submitting||uploading){snack('请等待本轮完成后使用命令');return}
  const match=value.match(/^\/(\S+)(?:\s+([\s\S]*))?$/);if(!match){updateCommands();return}
  let name=match[1],rest=match[2]||'';
  if(name==='skill'){const parts=rest.split(/\s+/);name=parts.shift();rest=parts.join(' ')}
  name=Object.keys(SKILL_NAMES).find(key=>SKILL_NAMES[key]===name)||name;
  const skill=skillCatalog.find(s=>s.name===name);
  if(!skill&&!COMMANDS.some(c=>c.name===name)){snack('未知命令。输入 / 查看可用命令');return}
  $('#input').value='';autosize();
  if(skill){if(!await activateSkill(name))return;if(rest){$('#input').value=rest;autosize();await send()}return}
  if(name==='new'){await newSession();return}
  if(name==='skills'){showTabAndOpen('skill');return}
  await ensureSession();
  if(name==='auto'){await activateSkill('');return}
  if(name==='rename'){openSessionDialog('rename',{session_id:sessionId,title:currentTitle});return}
  if(name==='clone'){await performMenuAction('clone',{session_id:sessionId,title:currentTitle});return}
  if(name==='context'){const data=await api('/sessions/'+sessionId),c=data.context,usage=data.usage||[],last=usage.findLast(x=>x.input_tokens_estimate);const prompt=usage.reduce((n,x)=>n+(x.prompt_tokens||0),0),cached=usage.reduce((n,x)=>n+(x.cached_tokens||0),0);snack(`对话记录约 ${c.estimated_tokens} token${last?` · 最近请求约 ${last.input_tokens_estimate} token`:''} · 已调用 ${usage.length} 轮${prompt?` · 输入 ${prompt} token${cached?`（缓存 ${cached}）`:''}`:''}`);return}
  if(name==='compact'){
    const id=sessionId,version=viewVersion;submitting=true;streaming=true;autosize();setConn('busy','整理上下文');
    try{const result=await api(`/sessions/${id}/compact`,{method:'POST'});if(version===viewVersion){submitting=false;await resumeSession(id);snack(`上下文已整理，完整记录保留（归档 ${result.archived_messages} 条）`)}}
    finally{if(version===viewVersion){submitting=false;streaming=false;restoreStatus()}}
  }
}

/* ---------- conversation model selection ---------- */
function hasImageInput(attached=[]){return manifests.some(item=>item.modality==='image')||attached.some(item=>item.modality==='image')||pendingFiles.some(file=>fileUploads.get(file).manifest?.modality==='image')}
async function ensureImageModel(attached){
  if(!hasImageInput(attached))return true;
  if(!llmModels.length)await loadLLMModels();
  if(modelCapabilities[currentLLMModel]?.image_input)return true;
  $('#llmSearch').value='';$('#llmPicker').showPopover();renderLLMModels();$('#llmSearch').focus();
  snack('选择看图模型后发送图片');return false;
}
function setConversationModel(name){currentLLMModel=name;$('#composerModel').textContent=name;$('#llmSelector').title='切换对话模型：'+name;$('#connChip').title='对话模型：'+name;renderPendingFiles()}
function renderLLMModels(){
  const query=$('#llmSearch').value.trim().toLowerCase(),box=$('#llmOptions');box.replaceChildren();
  const models=llmModels.filter(model=>model.toLowerCase().includes(query)&&(!hasImageInput()||modelCapabilities[model]?.image_input));
  if(!models.length){box.innerHTML='<div class="llm-empty">没有匹配的模型</div>';return}
  for(const model of models){
    const button=document.createElement('button');button.className='llm-option';button.dataset.model=model;button.setAttribute('role','option');button.setAttribute('aria-selected',String(model===currentLLMModel));
    button.innerHTML=`<span class="llm-name">${esc(model)}</span><span class="llm-tail">${modelCapabilities[model]?.image_input?'<span class="llm-default">看图</span>':''}${model===defaultLLMModel?'<span class="llm-default">默认</span>':''}${model===currentLLMModel?'<span class="ms">check_circle</span>':''}</span>`;
    button.onclick=()=>selectLLMModel(model).catch(reportError);box.appendChild(button);
  }
}
async function loadLLMModels(){
  const box=$('#llmOptions');box.innerHTML='<div class="llm-empty">读取模型…</div>';
  try{const result=await api('/llm/models');llmModels=result.models;modelCapabilities=result.capabilities||{};defaultLLMModel=result.default_model||'';renderLLMModels();renderPendingFiles()}
  catch(error){box.replaceChildren();const text=document.createElement('div');text.className='llm-empty';text.textContent=error.message;box.appendChild(text)}
}
async function selectLLMModel(model){
  if(changingModel)return;await ensureSession();const id=sessionId,version=viewVersion;changingModel=true;autosize();
  try{const result=await api(`/sessions/${id}/model`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({model})});
    if(version===viewVersion){setConversationModel(result.llm_model);$('#llmPicker').hidePopover();snack('已切换为 '+result.llm_model)}
  }finally{changingModel=false;autosize()}
}
$('#llmSelector').onclick=()=>{
  const picker=$('#llmPicker');if(picker.matches(':popover-open')){picker.hidePopover();return}
  $('#llmSearch').value='';picker.showPopover();$('#llmSearch').focus();loadLLMModels().catch(reportError);
};
$('#llmPicker').addEventListener('toggle',()=>$('#llmSelector').setAttribute('aria-expanded',String($('#llmPicker').matches(':popover-open'))));
$('#llmSearch').addEventListener('input',renderLLMModels);$('#refreshLLMModels').onclick=()=>loadLLMModels().catch(reportError);
$('#llmPicker').addEventListener('keydown',e=>{
  if(!['ArrowDown','ArrowUp'].includes(e.key))return;e.preventDefault();const options=$$('#llmOptions button'),index=options.indexOf(document.activeElement);
  if(options.length)options[(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length].focus();
});

/* ---------- user questions ---------- */
let inputQuestionIndex=0;
function inputAnswerText(request,answers){return request.questions.map(q=>q.question+'\n'+answers[q.id]).join('\n\n')}
function showInputQuestion(index){
  inputQuestionIndex=index;$$('#inputQuestions .question-group').forEach((group,n)=>group.hidden=n!==index);
  $('#questionTitle').textContent=pendingInput.questions[index].question;
  $('#questionCount').textContent=(index+1)+' / '+pendingInput.questions.length;$('#questionCount').hidden=pendingInput.questions.length===1;
  $('#questionPrev').hidden=index===0;$('#answerSubmitLabel').textContent=index<pendingInput.questions.length-1?'下一题':'提交并继续';$('#inputQuestions').scrollTop=0;
}
function renderInputRequest(request){
  pendingInput=request;inputQuestionIndex=0;const form=$('#inputRequest'),box=$('#inputQuestions');form.hidden=!request;box.replaceChildren();$('#questionPrev').hidden=true;
  if(!request)return;
  request.questions.forEach((question,index)=>{
    const group=document.createElement('fieldset');group.className='question-group';
    const options=question.answer_type==='choice'?question.options:[];
    const custom=`<textarea class="answer-custom ${options.length?'':'solo'}" rows="1" placeholder="输入回答…" aria-label="${esc(question.question)}"></textarea>`;
    group.innerHTML=`<legend>${esc(question.question)}</legend>${options.length?`<div class="answer-options">${options.map((option,n)=>`<label class="answer-option"><input type="radio" name="answer-${index}" value="${n}"><span><b>${esc(option.label)}</b>${option.description?`<small>${esc(option.description)}</small>`:''}</span></label>`).join('')}<label class="answer-option"><input type="radio" name="answer-${index}" value="custom" aria-label="填写自己的回答"><span><b>填写自己的回答</b>${custom}</span></label></div>`:custom}`;
    if(options.length)group.querySelector('.answer-custom').addEventListener('input',()=>{group.querySelector('[value=custom]').checked=true});box.appendChild(group);
  });
  showInputQuestion(0);
}
function inputQuestionAnswer(question,index){
  const group=$$('#inputQuestions .question-group')[index],choice=group.querySelector('input:checked');
  return question.answer_type!=='choice'||choice?.value==='custom'?group.querySelector('textarea').value.trim():choice?question.options[Number(choice.value)].label:'';
}
function focusUnansweredQuestion(index){
  const question=pendingInput.questions[index],group=$$('#inputQuestions .question-group')[index];showInputQuestion(index);snack('请回答：'+question.question);
  group.querySelector(question.answer_type==='choice'&&group.querySelector('input:checked')?.value!=='custom'?'input':'textarea').focus();
}
function submitInputRequest(e){
  e.preventDefault();
  if(!inputQuestionAnswer(pendingInput.questions[inputQuestionIndex],inputQuestionIndex)){focusUnansweredQuestion(inputQuestionIndex);return}
  if(inputQuestionIndex<pendingInput.questions.length-1){showInputQuestion(inputQuestionIndex+1);return}
  const answers={};
  for(const [index,question] of pendingInput.questions.entries()){
    const answer=inputQuestionAnswer(question,index);
    if(!answer){focusUnansweredQuestion(index);return}answers[question.id]=answer;
  }
  send({answers}).catch(reportError);
}
$('#questionPrev').onclick=()=>showInputQuestion(inputQuestionIndex-1);
$('#inputRequest').addEventListener('submit',submitInputRequest);

/* ---------- wiring ---------- */
$('#newSession').onclick=()=>newSession().catch(reportError);$('#newSession2').onclick=()=>{closeDrawer();newSession().catch(reportError)};$('#send').onclick=()=>{const action=streaming&&!$('#input').value.trim()&&!fileMentions.references().length?stopGeneration():send();action.catch(reportError)};
$('#drawerBtn').onclick=openDrawer;$('#drawerClose').onclick=closeDrawer;
document.body.classList.toggle('sidebar-collapsed',localStorage.getItem('eeg-sidebar-collapsed')==='true');
$('#drawerBtn').setAttribute('aria-expanded',String(innerWidth>900&&!document.body.classList.contains('sidebar-collapsed')));
const narrow=()=>matchMedia('(max-width:1200px)').matches;
function openSheet(){$('#drawer').classList.remove('open');if(innerWidth<=900)$('#drawerBtn').setAttribute('aria-expanded','false');$('#sheetBtn').setAttribute('aria-expanded','true');document.body.classList.add('sheet-open');$('#sheetBtn').classList.add('on');showTab(currentPanel);if(narrow())$('#scrim').style.display='block'}
function closeSheet(){resetAttachmentView();$('#sheetBtn').setAttribute('aria-expanded','false');document.body.classList.remove('sheet-open');$('#sheetBtn').classList.remove('on');$$('.workspace-nav [data-panel]').forEach(b=>b.classList.remove('active'));badge('#bData',+$('#bData').textContent||0);if(!$('#drawer').classList.contains('open'))$('#scrim').style.display='none'}
$('#sheetBtn').onclick=()=>document.body.classList.contains('sheet-open')?closeSheet():openSheet();$('#sheetClose').onclick=closeSheet;
$('#scrim').onclick=()=>{closeDrawer();if(narrow())closeSheet()};
function showTabAndOpen(name){if(name==='art'){openResults();return}showTab(name);openSheet()}
$('#input').addEventListener('input',autosize);$('#input').addEventListener('keydown',e=>{if(fileMentions.handleKey(e)||handleCommandKey(e))return;if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send().catch(reportError)}});
$('#attach').onclick=()=>$('#file').click();$('#file').onchange=e=>{queueFiles(e.target.files);e.target.value=''};
$('#input').addEventListener('paste',event=>{
  const images=[...event.clipboardData.items].filter(item=>item.kind==='file'&&item.type.startsWith('image/'));
  if(!images.length)return;
  event.preventDefault();
  const text=event.clipboardData.getData('text/plain');if(text)$('#input').setRangeText(text,$('#input').selectionStart,$('#input').selectionEnd,'end');
  const stamp=new Date().toISOString().replace(/[:.]/g,'-');
  queueFiles(images.map((item,index)=>{const file=item.getAsFile();return new File([file],`粘贴图片-${stamp}${index?'-'+(index+1):''}.${file.name.split('.').at(-1)||'png'}`,{type:file.type,lastModified:Date.now()})}));
});
$$('[data-model-task]').forEach(button=>button.onclick=()=>{modelTask=button.dataset.modelTask;$$('[data-model-task]').forEach(b=>b.classList.toggle('on',b===button));renderModels()});
$$('.tab').forEach(t=>t.onclick=()=>showTab(t.dataset.tab));
$('.tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const tabs=$$('.tab:not([hidden])'),index=tabs.indexOf(document.activeElement),next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;showTab(tabs[next].dataset.tab);tabs[next].focus()});
showTab('data');
$('#detailBack').onclick=()=>showTab(lastContentPanel);
document.addEventListener('click',e=>{const q=e.target.closest('[data-q]');if(q){$('#input').value=q.dataset.q;autosize();$('#input').focus()}});
const fld=$('#field');['dragenter','dragover'].forEach(t=>fld.addEventListener(t,e=>{e.preventDefault();fld.classList.add('drop')}));['dragleave','drop'].forEach(t=>fld.addEventListener(t,e=>{e.preventDefault();fld.classList.remove('drop')}));
fld.addEventListener('drop',e=>{
  const items=[...(e.dataTransfer.items||[])].filter(item=>item.kind==='file'),folders=items.filter(item=>item.webkitGetAsEntry?.()?.isDirectory).length;
  const files=[...e.dataTransfer.files].filter((_,i)=>!items[i]?.webkitGetAsEntry?.()?.isDirectory);
  if(folders)snack('文件夹不能直接上传；请从左侧挂接文件夹，再用 @ 引用其中的文件');
  if(files.length)queueFiles(files);
});
$('#themeBtn').onclick=()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'light';localStorage.setItem('eeg-theme',dark?'dark':'light');$('#themeBtn .ms').textContent=dark?'light_mode':'dark_mode'};
(()=>{const t=localStorage.getItem('eeg-theme')||'light';document.documentElement.dataset.theme=t;$('#themeBtn .ms').textContent=t==='dark'?'light_mode':'dark_mode'})();
api('/health').then(j=>{
  serviceState={worker:j.worker,model:j.model||'未配置'};
  $('#workerStatus').textContent=j.worker==='ok'?'本地服务':'服务未就绪';$('#workerDot').classList.toggle('on',j.worker==='ok');if(!currentLLMModel)setConversationModel(serviceState.model);
  $('#connChip').title='对话模型：'+currentLLMModel;
  if(!streaming&&!uploading)restoreStatus();
}).catch(()=>{serviceState={worker:'unavailable',model:'—'};$('#workerStatus').textContent='网关未启动';$('#workerDot').classList.remove('on');if(!streaming&&!uploading)restoreStatus()});
api('/models').then(result=>{modelCatalog=result.models;renderModels()}).catch(error=>{$('#modelList').textContent='无法读取模型目录：'+error.message});
api('/skills').then(list=>{skillCatalog=list;updateCommands();const box=$('#skillList');(list||[]).forEach(s=>{const c=document.createElement('div');c.className='card skillcard';c.innerHTML=`<div class="title-s">${esc(SKILL_NAMES[s.name]||s.name)}</div><div class="desc">${esc(s.description)}</div>${s.note?`<div class="warnline">${esc(s.note)}</div>`:''}<button class="chip small" data-activate-skill="${esc(s.name)}" style="margin-top:10px">选用此方法</button><details class="skill-tech"><summary><span class="ms">chevron_right</span>命令与工具</summary><div class="skill-command">/${esc(SKILL_NAMES[s.name]||s.name)}</div><div class="tags">${(s.allowed_tools||[]).map(t=>`<span class="src">${esc(TOOL_LABEL[t]||t)}</span>`).join('')}</div></details>`;box.appendChild(c)})}).catch(e=>{$('#skillList').textContent='无法加载分析能力：'+e.message});
refreshSessions().catch(reportError);
$('#sessionSearch').addEventListener('input',renderSessionList);
function toggleSessionSearch(open){const input=$('#sessionSearch');input.hidden=!open;$('#sessionSearchToggle').setAttribute('aria-expanded',String(open));if(open)input.focus();else{input.value='';renderSessionList()}}
$('#sessionSearchToggle').onclick=()=>toggleSessionSearch($('#sessionSearch').hidden);
$('#sessionSearch').addEventListener('keydown',event=>{if(event.key==='Escape'){toggleSessionSearch(false);$('#sessionSearchToggle').focus()}});
$$('[data-panel]').forEach(b=>b.onclick=()=>{closeDrawer();showTabAndOpen(b.dataset.panel)});
document.addEventListener('click',e=>{if(e.target.matches('.report-image'))lightbox(e.target.src)});
document.addEventListener('keydown',e=>{if($('#sessionDialog').open||e.defaultPrevented)return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();newSession().catch(reportError)}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='b'){e.preventDefault();openDrawer()}if(e.key==='Escape'){if(document.querySelector(':popover-open'))return;if($('#lightbox').style.display==='flex')$('#lightbox').style.display='none';else if(document.body.classList.contains('sheet-open'))closeSheet();else if($('#drawer').classList.contains('open'))closeDrawer()}});

document.addEventListener('click',e=>{const button=e.target.closest('[data-activate-skill]');if(button)activateSkill(button.dataset.activateSkill).then(()=>{if(narrow())closeSheet()}).catch(reportError)});
$('#trashToggle').onclick=()=>{trashView=!trashView;$('#sessionSearch').value='';updateSessionListHeader();refreshSessions().catch(reportError)};
$('#sessionMenu').addEventListener('click',e=>{const button=e.target.closest('[data-action]');if(!button)return;const record=menuRecord;$('#sessionMenu').hidePopover();if(!button.dataset.action.startsWith('export-'))performMenuAction(button.dataset.action,record).catch(reportError)});
$('#removeWorkspace').onclick=()=>{const record=menuWorkspace;$('#workspaceMenu').hidePopover();removeWorkspace(record).catch(reportError)};
$('#sessionMenu').addEventListener('keydown',e=>{const buttons=[...$('#sessionMenu').querySelectorAll('[role=menuitem]:not([hidden]):not(:disabled)')],index=buttons.indexOf(document.activeElement);if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();buttons[(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus()}});
window.addEventListener('resize',()=>{if($('#resultScopeMenu').matches(':popover-open'))$('#resultScopeMenu').hidePopover();if($('#llmPicker').matches(':popover-open'))$('#llmPicker').hidePopover();if($('#sessionMenu').matches(':popover-open'))$('#sessionMenu').hidePopover();if($('#workspaceMenu').matches(':popover-open'))$('#workspaceMenu').hidePopover()});
$('#dialogClose').onclick=$('#dialogCancel').onclick=()=>$('#sessionDialog').close();
$('#sessionForm').addEventListener('submit',async e=>{
  e.preventDefault();if(uploading||submitting||navigating){snack('文件或消息正在提交，请稍候');return}const {action,record}=dialogAction;$('#dialogSubmit').disabled=true;$('#dialogError').hidden=true;
  try{
    if(action==='rename'){
      const result=await api(`/sessions/${record.session_id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:$('#renameInput').value})});
      if(record.session_id===sessionId){currentTitle=result.title;$('#sessionLabel').textContent=currentTitle}$('#sessionDialog').close();await refreshSessions();snack('会话已重命名');
    }else{
      await api(`/sessions/${record.session_id}`,{method:'DELETE'});const wasCurrent=record.session_id===sessionId;
      if(wasCurrent)clearCurrentSession();$('#sessionDialog').close();await refreshSessions();snack('会话已移入回收站',{label:'撤销',run:()=>restoreSessionRecord(record.session_id,wasCurrent)});
    }
  }catch(error){$('#dialogError').textContent=error.message;$('#dialogError').hidden=false}
  finally{$('#dialogSubmit').disabled=false}
});
$('#mountWorkspace').onclick=async()=>{
  const button=$('#mountWorkspace');button.disabled=true;button.setAttribute('aria-busy','true');button.title='请在系统窗口中选择文件夹';
  try{await workspaceMount.openMount()}catch(error){reportError(error)}
  finally{button.disabled=false;button.removeAttribute('aria-busy');button.title='选择本地文件夹'}
};
const uploadProgress=document.createElement('div');uploadProgress.id='uploadProgress';uploadProgress.className='upload-progress';uploadProgress.hidden=true;$('#field').prepend(uploadProgress);
function refreshIcons(root){IconSystem.refresh(root);root.querySelectorAll('button[title]:not([aria-label])').forEach(button=>button.setAttribute('aria-label',button.title))}
refreshIcons(document);
new MutationObserver(changes=>{for(const c of changes){if(c.target.closest?.('#chat'))continue;if(c.target.matches?.('.ms'))refreshIcons(c.target);for(const node of c.addedNodes)if(node.nodeType===1&&!node.closest('#chat'))refreshIcons(node)}}).observe(document.body,{childList:true,subtree:true});
transcript=TranscriptUI.mount($('#chat'),{
  session:()=>sessionId,source:id=>knowledge[id],artifact:id=>artifacts[id],
  sourceTitle:SourceCitations.title,sourceLocation:SourceCitations.location,references:SourceCitations.references,
  artifactTitle,toolLabel:name=>TOOL_LABEL[name]||name,formatArgs:formatToolArgs,
  toolSummary:(name,result)=>['run_python','search_knowledge','knowledge_graph','plot'].includes(name)?TranscriptProjection.toolSummary(name,result):toolResultSummary(name,result),toolVisible:TranscriptProjection.toolVisible,
  openResults,openArtifact,openAttachment,openReference,openTrace:openExecutionDetails,
  copy:text=>navigator.clipboard.writeText(SourceCitations.copyText(text,knowledge,sessionId,location.origin)).then(()=>snack('已复制回复与引用')).catch(reportError),
  retry:async()=>{try{const snapshot=await api('/sessions/'+sessionId);if(snapshot.running)await resumeSession(sessionId);else await send({retry:true})}catch(error){reportError(error)}},changed:scroll
});
const saved=new URLSearchParams(location.search).get('session')||localStorage.getItem('eeg-session');
if(saved)resumeSession(saved).catch(e=>{localStorage.removeItem('eeg-session');history.replaceState(null,'','/');reportError(e)});
autosize();
