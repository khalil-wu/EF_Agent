function renderSession(j){
  resetView();setConversationModel(j.llm_model);currentTitle=j.title||'';currentParent=j.parent_session_id||'';$('#sessionLabel').textContent=currentTitle||'新分析';$('#branchLabel').hidden=!currentParent;
  for(const m of j.manifests||[])addManifest(m,j.previews?.[m.manifest_id]);
  for(const a of j.artifacts||[])addArtifactCard(a);
  for(const source of Object.values(j.sources||{}))addKnowledge(source,'snapshot');
  for(const source of j.knowledge||[])addKnowledge(source,'auto');
  for(const fact of j.knowledge_facts||[])addFact(fact);
  for(const message of j.messages||[])if(message.role==='tool'){
    const result=JSON.parse(message.content);
    for(const source of result.chunks||[])addKnowledge(source,'search');
    for(const fact of result.facts||[])addFact(fact);
  }
  setActiveSkill(j.active_skill||'');if(j.mode)mode=j.mode;
  transcript.hydrate(j);eventTraces.clear();liveCursor=j.live?{run_id:j.live.run_id,sequence:0}:null;streaming=j.running;requestFailure=j.last_error||'';
  for(const event of j.live?.events||[])consumeEngineEvent(event);
  if(j.live)liveCursor.sequence=j.live.sequence;
  renderInputRequest(j.pending_input||null);autosize();
  if(streaming)setConn('busy','正在分析');else restoreStatus();
}

function consumeEngineEvent(event){
  if(liveCursor?.run_id===event.run_id&&event.sequence<=liveCursor.sequence)return;
  if(event.run_id)liveCursor={run_id:event.run_id,sequence:event.sequence};
  switch(event.type){
    case 'tool_execution_start':{
      const args=parseArgs(event.args),entry=traceEv('run',TOOL_LABEL[event.tool_name]||event.tool_name,formatToolArgs(event.tool_name,args),args);
      entry.querySelector('.st').textContent='运行中';eventTraces.set(event.tool_call_id,entry);setConn('busy',TOOL_LABEL[event.tool_name]||'正在分析');break;
    }
    case 'tool_execution_end':{
      let entry=eventTraces.get(event.tool_call_id);
      if(!entry)entry=traceEv(event.is_error?'err':'ok',TOOL_LABEL[event.tool_name]||event.tool_name,'');
      entry.className='ev '+(event.is_error?'err':'ok');entry.querySelector('.st').textContent=event.is_error?'未完成':'完成';traceDetail(entry,'完整结果',event.result);eventTraces.delete(event.tool_call_id);
      if(['inspect_record','update_record_metadata'].includes(event.tool_name)&&!event.is_error&&event.result.manifest)addManifest(event.result.manifest);
      for(const source of event.result.chunks||[])addKnowledge(source,'search');
      for(const fact of event.result.facts||event.result.path||[])addFact(fact);
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
  }catch(error){if(version!==viewVersion)return;streaming=false;requestFailure=error.message;transcript.finish(error.message);restoreStatus()}
}

async function send(options={}){
  if(!options.retry&&$('#input').value.trim().startsWith('/')){await runSlashCommand($('#input').value.trim());return}
  const retry=options.retry===true,answered=options.answers?pendingInput:null;
  const draft=retry?'':answered?inputAnswerText(answered,options.answers):$('#input').value.trim(),selected=[...pendingFiles];
  if((!retry&&!draft&&!selected.length)||submitting||uploading||navigating)return;
  if(streaming&&selected.length){snack('本轮结束后再发送文件');return}
  if(retry&&streaming){snack('当前请求仍在运行');return}
  await ensureSession();if(submitting)return;submitting=true;autosize();
  const version=viewVersion,id=sessionId,wasStreaming=streaming;let ownsStream=false;
  try{
    const attached=selected.length?await uploadPending():[];if(version!==viewVersion)return;
    const text=retry?'':draft||'请检查本次上传的数据，概述结构和质量并建议下一步分析。';
    const view={text,attachments:attached.map(m=>({id:m.manifest_id,name:m.original_name||m.source_path.split(/[\\/]/).pop(),modality:m.modality})),questions:answered?answered.questions.map(q=>q.question):[]};
    if(answered)view.text=Object.values(options.answers).join('\n');
    if(!wasStreaming){requestFailure='';streamController=new AbortController();streaming=true;ownsStream=true;autosize();setConn('busy','正在分析')}
    const response=await fetch(`/sessions/${id}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,mode,retry,attachments:attached.map(m=>m.manifest_id),display_text:view.text,...(answered?{input_request_id:answered.id,answers:options.answers}:{})}),signal:wasStreaming?undefined:streamController.signal});
    if(version!==viewVersion)return;
    if(!response.ok){const error=await response.json();throw new Error(error.error||`请求失败 (${response.status})`)}
    if(!retry){renderInputRequest(null);$('#input').value='';transcript.addUser(view);if(!currentTitle){currentTitle=(draft||selected[0]?.name||'新分析').slice(0,40);$('#sessionLabel').textContent=currentTitle}}
    followBottom=true;submitting=false;autosize();
    if(response.status===202){snack('补充说明已送达');if(!wasStreaming){const snapshot=await api('/sessions/'+id);if(version!==viewVersion)return;renderSession(snapshot);watchSession(id,version)}return}
    liveCursor=null;eventTraces.clear();ownsStream=true;transcript.addTurn();
    await consumeEventStream(response,version);
  }catch(error){
    if(version!==viewVersion)return;requestFailure=error.message;
    if(ownsStream)transcript.finish(error.message);else snack(error.message);
  }finally{
    if(version===viewVersion){submitting=false;if(ownsStream){streaming=false;streamController=null;restoreStatus()}autosize();refreshSessions().catch(reportError)}
  }
}
