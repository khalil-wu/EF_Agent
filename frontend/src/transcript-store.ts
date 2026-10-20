export type Phase = 'pending' | 'commentary' | 'answer' | 'partial';
export type Message = {type:'message';id:string;text:string;phase:Phase;done:boolean};
export type Progress = {stage:string;label:string;detail:string;ts?:string};
export const REASONING_PREVIEW_MS=5000;
export type ReasoningPreview = {messageId:string;text:string;updatedAt:number};
export type Tool = {type:'tool';id:string;name:string;args:any;status:'preparing'|'running'|'done'|'error'|'interrupted';result?:any;progress?:Progress[]};
export type Turn = {type:'turn';id:string;entries:(Message|Tool)[];artifacts:string[];running:boolean;open?:boolean;waiting:boolean;stopped:boolean;error?:string;activity?:'thinking';reasoning?:ReasoningPreview;startedAt?:number;finishedAt?:number};
export type FileReference = {workspace_id?:string;manifest_id?:string;path?:string;name:string;kind:string;excluded?:boolean};
export type User = {type:'user';id:string;text:string;attachments:{id:string;name:string;modality:string}[];references:FileReference[];questions:string[]};
export type Row = User|Turn;
export type Event = {type:string;message_id?:string;tool_call_id?:string;tool_name?:string;text?:string;phase?:string;args?:any;result?:any;is_error?:boolean;[key:string]:any};
export type State = {rows:Row[];metadataVersion:number;contentVersion:number};
const phase = (value:string):Phase => ({pending:'pending',commentary:'commentary',final_answer:'answer',interrupted:'partial'} as const)[value as 'pending'] || 'answer';

export function createTranscriptStore(){
  let state:State={rows:[],metadataVersion:0,contentVersion:0},counter=0,current:string|null=null;
  const listeners=new Set<()=>void>();
  function emit(){for(const listener of listeners)listener()}
  function update(id:string,change:(turn:Turn)=>Turn,content=true){state={...state,contentVersion:state.contentVersion+Number(content),rows:state.rows.map(row=>row.id===id?change(row as Turn):row)};emit()}
  function turn(){if(!current)addTurn();return state.rows.find(row=>row.id===current) as Turn}
  function addTurn(id='turn-'+(++counter),running=true){current=id;state={...state,contentVersion:state.contentVersion+1,rows:[...state.rows.map(row=>row.type==='turn'?{...row,running:false}:row),{type:'turn',id,entries:[],artifacts:[],running,waiting:false,stopped:false,startedAt:running?Date.now():undefined}]};emit();return id}
  function addUser(view:any,id='user-'+(++counter)){current=null;state={...state,contentVersion:state.contentVersion+1,rows:[...state.rows.map(row=>row.type==='turn'&&row.waiting?{...row,waiting:false}:row),{type:'user',id,text:view.text,attachments:view.attachments||[],references:view.references||[],questions:view.questions||[]}]};emit()}
  function owner(id:string){return state.rows.find(row=>row.type==='turn'&&row.entries.some(entry=>entry.id===id)) as Turn|undefined}
  function message(id:string,text:string,kind:Phase,done:boolean){const active=owner(id)||turn();const existing=active.entries.find(entry=>entry.id===id);update(active.id,row=>({...row,entries:existing?row.entries.map(entry=>entry.id===id?{...entry,text,phase:kind,done} as Message:entry):[...row.entries,{type:'message',id,text,phase:kind,done}]}))}
  function tool(id:string,name:string,args:any,status:Tool['status'],result?:any,messageId?:string){const active=owner(id)||owner(messageId!)||turn(),existing=active.entries.find(entry=>entry.id===id);update(active.id,row=>({...row,entries:existing?row.entries.map(entry=>entry.id===id?{...entry,name,args:args??(entry as Tool).args,status,result,progress:result?.execution_progress||(entry as Tool).progress} as Tool:entry):[...row.entries,{type:'tool',id,name,args,status,result,progress:result?.execution_progress}]}))}
  function artifact(id:string,messageId?:string){
    const source=owner(messageId!);
    if(source?.entries.some(entry=>entry.id===messageId&&entry.type==='tool'&&entry.name==='read_artifact')||state.rows.some(row=>row.type==='turn'&&row.artifacts.includes(id)))return;
    const active=source||turn();update(active.id,row=>({...row,artifacts:[...row.artifacts,id]}));
  }
  function finish(error?:string){state={...state,contentVersion:state.contentVersion+1,rows:state.rows.map(row=>row.type==='turn'?{...row,running:false,activity:undefined,finishedAt:row.running?Date.now():row.finishedAt,error:row.id===current?error||row.error:row.error,entries:row.entries.map(entry=>entry.type==='message'&&!entry.done?{...entry,phase:'partial',done:true}:entry.type==='tool'&&(entry.status==='preparing'||entry.status==='running')?{...entry,status:'interrupted'}:entry)}:row)};emit()}
  function apply(event:Event){
    switch(event.type){
      case 'agent_start':case 'turn_start':{const active=turn();update(active.id,row=>({...row,running:true}));break}
      case 'message_started':message(event.message_id!,'','pending',false);break;
      case 'model_activity':{const active=owner(event.message_id!)||turn();update(active.id,row=>({...row,activity:'thinking'}));break}
      case 'reasoning_delta':{
        const updatedAt=Date.parse(event.ts);
        if(Date.now()-updatedAt>=REASONING_PREVIEW_MS)break;
        const active=owner(event.message_id!)!;
        update(active.id,row=>({...row,reasoning:{messageId:event.message_id!,text:((row.reasoning&&row.reasoning.messageId===event.message_id&&updatedAt-row.reasoning.updatedAt<REASONING_PREVIEW_MS?row.reasoning.text:'')+event.text!).slice(-600),updatedAt}}),false);
        break;
      }
      case 'message_delta':{const item=owner(event.message_id!)!.entries.find(entry=>entry.id===event.message_id) as Message;message(item.id,item.text+event.text!,item.phase,false);break}
      case 'message_phase':case 'message_completed':{const item=owner(event.message_id!)!.entries.find(entry=>entry.id===event.message_id) as Message;message(item.id,item.text,phase(event.phase!),event.type==='message_completed');break}
      case 'tool_call_started':tool(event.tool_call_id!,event.tool_name!,{},'preparing',undefined,event.message_id);break;
      case 'tool_execution_start':tool(event.tool_call_id!,event.tool_name!,typeof event.args==='string'?JSON.parse(event.args):event.args,'running',undefined,event.message_id);break;
      case 'tool_execution_progress':{const active=owner(event.tool_call_id!)!;update(active.id,row=>({...row,entries:row.entries.map(entry=>entry.id===event.tool_call_id?{...entry,progress:(entry as Tool).progress?.some(stage=>stage.stage===event.result.stage)?(entry as Tool).progress!.map(stage=>stage.stage===event.result.stage?event.result:stage):[...((entry as Tool).progress||[]),event.result]} as Tool:entry)}));break}
      case 'tool_execution_end':{const item=owner(event.tool_call_id!)?.entries.find(entry=>entry.id===event.tool_call_id) as Tool;if(item)tool(item.id,event.tool_name!,item.args,event.is_error?'error':'done',event.result);break}
      case 'artifact_ready':artifact(event.result.artifact_id,event.message_id||event.tool_call_id);break;
      case 'user_input_requested':{const active=turn();update(active.id,row=>({...row,waiting:true}));break}
      case 'error':turn();finish(event.text);break;
      case 'stopped':{const active=turn();update(active.id,row=>({...row,stopped:true}));finish();break}
      case 'session_saved':finish();break;
    }
  }
  function hydrate(snapshot:any){
    state={rows:[],metadataVersion:state.metadataVersion+1,contentVersion:state.contentVersion+1};current=null;counter=0;
    const tools=new Map<string,{name:string;args:any}>();
    (snapshot.messages||[]).forEach((value:any,index:number)=>{
      if(value.role==='user'){
        if(/^<(reflection|compaction_summary|file_context|latest_context)>/.test(value.content))return;
        if(current)finish();addUser(value.presentation||{text:value.content},'user-history-'+index);return;
      }
      if(value.role==='assistant'){
        if(!current)addTurn('turn-history-'+index,false);
        const kind=value.phase?phase(value.phase):value.tool_calls?.length?'commentary':'answer';
        message(value.message_id||'message-history-'+index,value.content||'',kind,true);
        for(const call of value.tool_calls||[]){const args=call.arguments_error?{}:typeof call.function.arguments==='string'?JSON.parse(call.function.arguments):call.function.arguments;tools.set(call.id,{name:call.function.name,args});tool(call.id,call.function.name,args,call.arguments_error?'error':'preparing',call.arguments_error?{status:'error',error:{code:'bad_args',message:call.arguments_error}}:undefined)}
      }
      if(value.role==='tool'&&tools.has(value.tool_call_id)){const result={...JSON.parse(value.content),...value.tool_presentation},call=tools.get(value.tool_call_id)!;tool(value.tool_call_id,call.name,call.args,result.status==='error'?'error':'done',result);if(result.artifact)artifact(result.artifact.artifact_id,value.tool_call_id);for(const item of result.artifacts||[])artifact(item.artifact_id,value.tool_call_id);for(const item of result.result_links||[])artifact(item.artifact_id,value.tool_call_id)}
    });
    if(snapshot.running){const active=turn();update(active.id,row=>({...row,running:true,startedAt:snapshot.live?.started_at?Date.parse(snapshot.live.started_at):Date.now()}))}else if(current||snapshot.last_error||snapshot.last_stopped){turn();finish(snapshot.last_error)}
    if(snapshot.pending_input){const active=turn();update(active.id,row=>({...row,waiting:true}))}
    if(current&&snapshot.last_stopped)update(current,row=>({...row,stopped:true}));
    emit();
  }
  return {getSnapshot:()=>state,subscribe(listener:()=>void){listeners.add(listener);return()=>listeners.delete(listener)},hydrate,addUser,addTurn,message,tool,artifact,apply,finish,
    reset(){state={rows:[],metadataVersion:state.metadataVersion+1,contentVersion:state.contentVersion+1};current=null;counter=0;emit()},
    toggle(id:string){update(id,row=>({...row,open:!(row.open??row.waiting)}),false)},
    engage(id:string){const row=state.rows.find(item=>item.id===id) as Turn;if(row.open!==true)update(id,turn=>({...turn,open:true}),false)},
    metadata(){state={...state,metadataVersion:state.metadataVersion+1};emit()}
  };
}
