const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync,existsSync}=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=readFileSync(path.join(__dirname,'../frontend/bridge/app.js'),'utf8');

test('history replay registers immutable source snapshots before the newer retrieval cache',()=>{
 const registry={};
 const replay={fileMentions:{save(){},restore(){},clear(){},references:()=>[],update(){}},reportError(){},resetView(){},setConversationModel(){},setActiveSkill(){},$:()=>({textContent:'',hidden:false}),addKnowledge(item){registry[item.chunk_id]??=item},transcript:{hydrate(){}},renderResultCatalog(){},eventTraces:new Map(),renderInputRequest(){},autosize(){},setConn(){},restoreStatus(){}};
 vm.runInNewContext(source.slice(source.indexOf('function renderSession('),source.indexOf('function consumeEngineEvent(')),replay);
 replay.renderSession({sources:{k_abcdef:{chunk_id:'k_abcdef',text:'原始引文'}},knowledge:[{chunk_id:'k_abcdef',text:'后来的修改'}],messages:[],running:false});
 assert.equal(registry.k_abcdef.text,'原始引文');
});
const context={artifacts:{},knowledge:{},SKILL_NAMES:{artifact_quality:'信号质量'},SourceCitations:require('../gateway/internal/server/static/citations.js')};

test('legacy rejected arguments remain inspectable without breaking the session shell',()=>{
 const raw='{"code":"'+'x'.repeat(3177),details=[],entries=[];
 const replay={fileMentions:{restore(){},update(){}},resetView(){},setConversationModel(){},setActiveSkill(){},
  $:()=>({textContent:'',hidden:false}),transcript:{hydrate(snapshot){assert.equal(snapshot.messages.length,3)}},renderResultCatalog(){},eventTraces:new Map(),
  renderInputRequest(){},autosize(){},setConn(){},restoreStatus(){},TOOL_LABEL:{run_python:'执行分析脚本'},
  parseArgs(){assert.fail('marked invalid arguments must not be parsed')},
  traceEv(){const status={textContent:''};const entry={className:'ev',classList:{contains:()=>false},querySelector:()=>status};entries.push(entry);return entry},
  traceDetail(entry,label,value){details.push([label,value]);return {dataset:{}}}
 };
 vm.runInNewContext(source.slice(source.indexOf('function renderSession('),source.indexOf('function consumeEngineEvent(')),replay);
 replay.renderSession({running:false,messages:[
  {role:'user',content:'处理数据'},
  {role:'assistant',tool_calls:[{id:'bad',arguments_error:'调用参数不完整',function:{name:'run_python',arguments:raw}}]},
  {role:'tool',tool_call_id:'bad',content:JSON.stringify({status:'error',error:{code:'bad_args',message:'未执行'}})}
 ]});
 assert.equal(entries.length,1);assert.equal(replay.eventTraces.get('bad'),entries[0]);
 assert.equal(details.find(([label])=>label==='未解析的调用参数')[1],raw);
 assert.equal(entries[0].className,'ev err');
 assert.equal(entries[0].querySelector('.st').textContent,'未执行');
});
vm.runInNewContext(source.slice(source.indexOf('function esc('),source.indexOf('function autosize(')),context);
vm.runInNewContext(source.slice(source.indexOf('const STEP_LABEL='),source.indexOf('/* ---------- trace ---------- */')),context);
vm.runInNewContext(source.slice(source.indexOf('function sourceExcerptHTML('),source.indexOf('function sourcePreview(')),context);

const science={TranscriptUI:require('../frontend/src/result-catalog.ts'),manifests:[],esc:context.esc,MODEL_TASK_LABEL:{emotion:'情绪识别'},KIND_LABEL:{prediction:'预测'},PLOT_LABEL:{}};
vm.runInNewContext(source.slice(source.indexOf('function coordinateLabel('),source.indexOf('function openArtifact(')),science);

test('emotion states and epoch coordinates do not claim continuous model results',()=>{
 const coordinates=[{epoch_index:0,epoch_relative_time_range_s:[-1,3]},{epoch_index:1,epoch_relative_time_range_s:[-1,3]}];
 const coverage={covered_duration_s:8,n_windows:2,complete:true,original_time_ranges_s:coordinates};
 const html=science.emotionSummary({assessment_status:'unassessed',label:null,rationale:'测量记录',coverage,prediction_preview:[]});
 assert.match(html,/测量审阅/);assert.match(html,/Epoch 0/);assert.match(html,/Epoch 1/);assert.match(html,/-1–3/);
 assert.doesNotMatch(html,/0–8|基于 EEG 特征的工具推断|情绪判断/);
 assert.match(science.emotionSummary({assessment_status:'hypothesis',label:'HVHA',rationale:'候选',coverage,prediction_preview:[]}),/特征候选/);
 assert.match(science.coordinatePreview(coordinates,6),/显示 2 \/ 6 段/);
});

test('prediction cards distinguish logits, probabilities and named regression units',()=>{
 const base={model_id:'fixture',model_version:'test-only',task:'emotion',output_kind:'classification',labels:['negative','positive'],
             score_kind:'logits',calibration:{status:'unknown'},aggregation:null,prediction_count:20,
             prediction_preview:[{window_index:0,epoch_index:1,epoch_relative_time_range_s:[-1,1],label:'positive',scores:[-2,4]}]};
 const html=science.predictionSummary(base);assert.match(html,/logits/);assert.match(html,/<b>positive<\/b>[\s\S]*?<em>4\.000<\/em>/);assert.match(html,/1 \/ 20/);assert.match(html,/校准状态未知/);
 assert.match(science.predictionSummary({...base,score_kind:'probabilities',prediction_preview:[{...base.prediction_preview[0],scores:[.2,.8]}]}),/类别概率/);
 const regression=science.predictionSummary({...base,output_kind:'regression',dimensions:[{name:'valence',unit:'rating_1_9'}],prediction_preview:[{window_index:0,values:[6]}]});
 assert.match(regression,/valence 6 rating_1_9/);assert.doesNotMatch(regression,/logits|类别概率/);
 assert.equal(science.artifactTitle({kind:'prediction',summary:{method:'script'}}),'预测产物');
});

test('quality cards show separate initial and line-noise observation ranges',()=>{
 const html=science.qualitySummary({sample_count:1000,flat_channels:[],line_noise_ratio:.02,line_noise_bands_hz:[[48,52]],
  sample_original_time_ranges_s:[{original_time_range_s:[0,10]}],line_noise_original_time_ranges_s:[{original_time_range_s:[0,4]}]},
  {channel_names:['F3'],channel_types:['eeg'],signal_kind:'continuous'});
 assert.match(html,/开头初筛/);assert.match(html,/0–10/);assert.match(html,/工频检查.*0–4/);assert.match(html,/检查范围内未发现/);
});

test('live history replay reuses tool-call detail and stopping ends active trace entries',()=>{
 let created=0;
 const replay={$:()=>({style:{}}),eventTraces:new Map(),liveCursor:null,stopRequested:true,streaming:true,requestFailure:'',
  parseArgs:value=>typeof value==='string'?JSON.parse(value):value,TOOL_LABEL:{read_artifact:'读取结果'},formatToolArgs:()=>'',
  traceEv(){created++;const fields={};return {className:'ev run',classList:{contains(){return this.owner.className.includes('run')}},
   querySelector(selector){if(selector==='[data-tool-result]'||selector==='[data-tool-args]')return null;return fields[selector]??={textContent:''}}}},
  traceDetail(){return {dataset:{}}},setConn(){},restoreStatus(){},transcript:{apply(){}},scroll(){},autosize(){}};
 vm.runInNewContext(source.slice(source.indexOf('function consumeEngineEvent('),source.indexOf('async function watchSession(')),replay);
 replay.consumeEngineEvent({type:'tool_call_started',tool_call_id:'call-1',tool_name:'read_artifact',run_id:'run-1',sequence:0});
 const existing=replay.eventTraces.get('call-1');existing.classList.owner=existing;existing.hidden=true;
 replay.consumeEngineEvent({type:'tool_execution_start',tool_call_id:'call-1',tool_name:'read_artifact',args:{},run_id:'run-1',sequence:1});
 assert.equal(created,1);assert.equal(existing.hidden,false,'a cleared preparing row must become visible when execution starts');
 replay.consumeEngineEvent({type:'stopped',run_id:'run-1',sequence:2});
 assert.equal(replay.streaming,false);assert.equal(replay.stopRequested,false);assert.equal(existing.className,'ev stopped');
});

test('a failed stop request restores controls while an accepted stop waits for the terminal event',async()=>{
 const runtime={stopRequested:false,streaming:true,sessionId:'fixture',autosize(){},setConn(){},api:async()=>{throw new Error('stop failed')}};
 vm.runInNewContext(source.slice(source.indexOf('async function stopGeneration('),source.indexOf('async function send(')),runtime);
 await assert.rejects(runtime.stopGeneration(),/stop failed/);
 assert.equal(runtime.stopRequested,false);assert.equal(runtime.streaming,true);
 runtime.api=async()=>({status:'stopping'});await runtime.stopGeneration();
 assert.equal(runtime.stopRequested,true);assert.equal(runtime.streaming,true);
});

test('PDF excerpt display reflows layout lines without modifying the evidence snapshot',()=>{
 const raw='Following\nan\norderly approach\nis recommended.\n\nRetain <literal> text.';
 const evidence={title:'Guidelines.pdf',source_kind:'user_upload',text:raw};
 assert.equal(context.sourceExcerptHTML(evidence),'<p>Following an orderly approach is recommended.</p><p>Retain &lt;literal&gt; text.</p>');
 assert.equal(evidence.text,raw);
 assert.match(context.sourceExcerptHTML({title:'方法笔记',source_kind:'project_note',text:'## 方法\n\n**观察**'}),/<h2>方法<\/h2>/);
});

test('reviewed note links open delivered originals without changing the saved excerpt',()=>{
 const raw='[Original method](../papers/original.md#Methods:~:text=verified%20sentence)';
 const note={chunk_id:'k_123abc',source_kind:'project_note',text:raw};
 context.knowledge.k_456def={chunk_id:'k_456def',doc_id:'original',section:'Methods',supports_notes:['k_123abc'],title:'Original study'};
 assert.match(context.sourceExcerptHTML(note),/data-know="k_456def"/);
 assert.equal(note.text,raw);
 delete context.knowledge.k_456def;
 assert.match(context.sourceExcerptHTML(note),/原文片段未随本次检索提供/);
 assert.doesNotMatch(context.sourceExcerptHTML(note),/href=.*papers\/original/);
});

test('artifact links are generated once, without nested anchor markup',()=>{
 const html=context.md('artifact://art_abcdef and [report](artifact://art_123456)');
 assert.equal((html.match(/<a /g)||[]).length,2);
 assert.match(html,/data-art="art_123456">report<\/a>/);
 assert.doesNotMatch(html,/data-art="<a/);
});
test('generated images open through attachments while raw HTML stays escaped',()=>{
 const html=context.md('![波形](artifact://art_abcdef)\n\n<script>alert(1)</script>');
 assert.match(html,/<a class="art" href="#" data-art="art_abcdef">波形<\/a>/);
 assert.doesNotMatch(html,/<img/);
 assert.doesNotMatch(html,/<script>/);
});
test('report preview renders artifact images inside the report',()=>{
 const html=context.md('![功率谱](artifact://art_abcdef)',{images:true});
 assert.match(html,/<img class="report-image" src="\/artifacts\/art_abcdef\/file" alt="功率谱">/);
});
test('scientific figures remain independent blocks between prose paragraphs',()=>{
 const html=context.md('实测谱峰如下。\n![功率谱](artifact://art_abcdef)\n\n峰值是 10 Hz。',{images:true});
 assert.match(html,/<\/p><figure class="response-figure">/);
 assert.match(html,/<\/figure><p>峰值是 10 Hz。<\/p>/);
 assert.doesNotMatch(html,/<p><figure/);
});
test('code does not become links and quotes cannot escape attributes',()=>{
 const html=context.md('`artifact://art_abcdef` [site](https://example.com/"test) [k_123456]');
 assert.match(html,/<code>artifact:\/\/art_abcdef<\/code>/);
 assert.match(html,/&quot;test/);
 assert.match(html,/data-know="k_123456"/);
});

vm.runInNewContext(source.slice(source.indexOf('function idleConnection('),source.indexOf('function setConn(')),context);
test('a late healthy worker response cannot overwrite the failed model state',()=>{
 const health={worker:'ok',model:'glm-5.3-flash'};
 assert.equal(context.idleConnection(health,'llm 522').state,'error');
 assert.equal(context.idleConnection(health,'llm 522').text,'上次请求失败');
 assert.equal(context.idleConnection(health,'').text,'glm-5.3-flash');
 assert.equal(context.idleConnection({worker:'unavailable',model:'glm'},'').text,'本地服务未就绪');
});

test('analysis tools show Chinese summaries instead of raw parameters',()=>{
 context.manifests=[{m:{manifest_id:'doc_example',original_name:'采集说明.pdf'}}];
 const args={source_id:'sig_123',steps:[{op:'notch',freq:50},{op:'bandpass',low:0.5,high:40},{op:'resample',target_hz:128}]};
 assert.equal(context.formatToolArgs('preprocess',args),'工频陷波 · 50 Hz → 带通滤波 · 0.5–40 Hz → 重采样 · 128 Hz');
 assert.equal(context.formatToolArgs('plot',{kind:'psd',start_s:0,end_s:10}),'功率谱 · 0–10 秒');
 assert.equal(context.formatToolArgs('read_skill',{name:'artifact_quality'}),'信号质量');
 assert.equal(context.formatToolArgs('read_document',{page:2,limit:3}),'第 2 页起 · 最多 3 页');
 assert.equal(context.formatToolArgs('read_document',{manifest_id:'doc_example',page:2,limit:3}),'采集说明.pdf · 第 2 页起 · 最多 3 页');
 assert.equal(context.formatToolArgs('recognize_emotion',{start_s:10,end_s:30,video_manifest_id:'paired-video'}),'使用脑电与配对视频 · 10–30 秒');
 assert.equal(context.formatToolArgs('recognize_emotion',{}),'使用脑电');
});
vm.runInNewContext(source.slice(source.indexOf('function toolResultSummary('),source.indexOf('/* ---------- manifests ---------- */')),context);
test('emotion recognition reports the actual prediction count in the saved result',()=>{
 assert.equal(context.toolResultSummary('recognize_emotion',{summary:{prediction_count:8}}),'已保存 8 个窗口的识别结果');
 assert.equal(context.toolResultSummary('recognize_emotion',{summary:{}}),'');
});

test('plain artifact ids are presented as links with readable labels',()=>{
 assert.match(context.md('结果：art_abcdef'),/data-art="art_abcdef">查看产物<\/a>/);
});

test('uploaded document citations display their source page',()=>{
 context.knowledge.k_abcdef={scope:'session',title:'论文.pdf',page:2,text:'实际读取的原文。',source:'论文.pdf · 第 2 页'};
 assert.match(context.md('[k_abcdef]'),/aria-label="来源 1：论文.pdf · 第 2 页"/);
 assert.doesNotMatch(context.md('[k_abcdef]').replace(/<[^>]*>/g,''),/k_abcdef/);
});

test('labelled source citations from the model open the evidence fragment',()=>{
 assert.match(context.md('[第 2 页](k_abcdef)'),/data-know="k_abcdef"[^>]*aria-label="来源 1：论文.pdf · 第 2 页"/);
});

test('bare and bracketed citations resolve to one source number while code stays literal',()=>{
 const numbers=new Map();
 const html=context.md('k_abcdef 与 [k_abcdef]，以及 `k_abcdef`',{citationNumbers:numbers});
 assert.equal(numbers.size,1);
 assert.equal((html.match(/class="citation-ref"/g)||[]).length,2);
 assert.match(html,/<code>k_abcdef<\/code>/);
});

test('invented citation ids stay unresolved and do not receive source numbers',()=>{
 const numbers=new Map(),html=context.md('来源 [k_deadbeef]',{citationNumbers:numbers});
 assert.equal(numbers.size,0);
 assert.match(html,/citation-ref unavailable/);
 assert.doesNotMatch(html,/来源 1/);
});

vm.runInNewContext(source.slice(source.indexOf('const SRC_LABEL='),source.indexOf('function renderManifest(')),context);
test('format matching is labelled as an assumption without a certainty percentage',()=>{
 const html=context.srcTag({source:'dataset_adapter',confidence:1});
 assert.match(html,/格式匹配/);
 assert.doesNotMatch(html,/100%/);
 const detail=context.metadataEvidence({format_profile:'deap_preprocessed',dataset:'unknown',metadata_evidence:{origin:{value:'unverified',source:'unknown'},sample_rate_hz:{value:128,source:'format_assumption'}}});
 assert.match(detail,/格式候选/);
 assert.match(detail,/未核验/);
 assert.match(detail,/格式假设/);
});
test('legacy dataset metadata remains a registration without source verification',()=>{
 const detail=context.metadataEvidence({dataset:'DEAP'});
 assert.match(detail,/数据集登记/);
 assert.match(detail,/未核验/);
});

function transcriptNode(className=''){
 const node={className,children:[],parentElement:null,textContent:'',selectors:{},hidden:false,
  appendChild(child){return this.insertBefore(child,null)},
  insertBefore(child,before){if(child.parentElement)child.parentElement.children.splice(child.parentElement.children.indexOf(child),1);this.children.splice(before?this.children.indexOf(before):this.children.length,0,child);child.parentElement=this;return child},
  querySelector(selector){return this.selectors[selector]||null},
  querySelectorAll(selector){return this.children.filter(child=>child.classList.contains(selector.slice(1)))},
  remove(){this.parentElement.children.splice(this.parentElement.children.indexOf(this),1)},
 };
 node.classList={contains:name=>node.className.split(' ').includes(name),toggle(name,on){const names=new Set(node.className.split(' '));on?names.add(name):names.delete(name);node.className=[...names].join(' ')},remove(name){this.toggle(name,false)},add(name){this.toggle(name,true)}};
 return node;
}

test('question cards advance one at a time, retain answers on back navigation and submit together',()=>{
 const box=transcriptNode(),form={addEventListener(name,handler){this[name]=handler}},prev={},count={},label={},sent=[],notices=[];
 box.replaceChildren=()=>{box.children=[]};
 const elements={'#inputRequest':form,'#inputQuestions':box,'#questionPrev':prev,'#questionCount':count,'#questionTitle':{},'#answerSubmitLabel':label};
 const runtime={pendingInput:null,$:selector=>elements[selector],$$:()=>box.children,esc:context.esc,snack:message=>notices.push(message),reportError:assert.fail,send(options){sent.push(JSON.parse(JSON.stringify(options)));return Promise.resolve()},document:{createElement(){
   const group=transcriptNode(),textarea={value:'',addEventListener(){},focus(){group.focused=true}};
   group.querySelector=selector=>selector==='input:checked'?group.choice||null:selector==='[value=custom]'?group.customRadio:textarea;group.customRadio={checked:false};return group;
 }}};
 vm.runInNewContext(source.slice(source.indexOf('/* ---------- user questions ---------- */'),source.indexOf('/* ---------- wiring ---------- */')),runtime);
 runtime.renderInputRequest({id:'ask_eeg',questions:[{id:'source',question:'来源是什么？',answer_type:'choice',options:[{label:'官方文件'},{label:'用户副本'},{label:'合成数据'}]},{id:'rate',question:'采样率是多少？',answer_type:'number'}]});
 const first=box.children[0],second=box.children[1],submit=()=>form.submit({preventDefault(){}});
 assert.equal(first.hidden,false);assert.equal(second.hidden,true);assert.equal(count.textContent,'1 / 2');assert.equal(prev.hidden,true);assert.equal(label.textContent,'下一题');
 submit();assert.equal(notices.length,1);assert.equal(sent.length,0);assert.equal(first.focused,true);
 first.choice={value:'1'};box.scrollTop=90;submit();
 assert.equal(first.hidden,true);assert.equal(second.hidden,false);assert.equal(count.textContent,'2 / 2');assert.equal(label.textContent,'提交并继续');assert.equal(prev.hidden,false);assert.equal(box.scrollTop,0);assert.equal(sent.length,0);
 second.querySelector('textarea').value='128';prev.onclick();
 assert.equal(first.hidden,false);assert.equal(first.choice.value,'1');assert.equal(second.querySelector('textarea').value,'128');
 first.choice.value='2';submit();
 first.choice=null;submit();
 assert.equal(first.hidden,false);assert.equal(second.hidden,true);assert.equal(sent.length,0);
 first.choice={value:'2'};submit();submit();
 assert.deepEqual(sent,[{answers:{source:'合成数据',rate:'128'}}]);
 runtime.renderInputRequest({id:'ask_single',questions:[{id:'unit',question:'单位是什么？',answer_type:'text'}]});
 assert.equal(box.children[0].hidden,false);assert.equal(count.hidden,true);assert.equal(prev.hidden,true);assert.equal(label.textContent,'提交并继续');
 runtime.renderInputRequest(null);assert.equal(form.hidden,true);assert.equal(box.children.length,0);
});

function attachmentRuntime(session='session-one'){
 const elements=new Map(),requests=[],messages=[],users=[],notices=[],apiCalls=[];
 function node(){const value=transcriptNode();Object.assign(value,{style:{},value:'',scrollHeight:30,disabled:false,attributes:{},showPopover(){this.open=true},focus(){this.focused=true},setAttribute(name,text){this.attributes[name]=text},append(...children){children.forEach(child=>this.appendChild(child))},replaceChildren(...children){this.children=[];this.append(...children)}});return value}
 const element=selector=>{if(!elements.has(selector))elements.set(selector,node());return elements.get(selector)};
 class MockFormData{constructor(){this.fields=new Map()}append(name,value){this.fields.set(name,value)}}
 class MockXHR{
  constructor(){this.upload={}}
  open(method,url){this.method=method;this.url=url}
  send(body){this.body=body;requests.push(this)}
  bytesSent(){this.upload.onload()}
  complete(response,status=200){this.response=response;this.status=status;this.onload()}
 }
 const runtime={fileMentions:{save(){},restore(){},clear(){},references:()=>[],update(){}},currentWorkspaceID:'',sessionId:session,viewVersion:0,pendingFiles:[],pendingFilesBySession:new Map(),fileUploads:new WeakMap(),uploadTask:null,uploading:false,submitting:false,navigating:false,streaming:false,stopRequested:false,changingModel:false,creating:null,streamController:null,pendingInput:null,currentTitle:'',currentParent:'',currentLLMModel:'test',mode:'readonly',trashView:false,requestFailure:'',followBottom:true,liveCursor:null,eventTraces:new Map(),
  manifests:[],llmModels:['test'],modelCapabilities:{test:{image_input:true}},renderLLMModels(){},
  $:element,$$:()=>[],document:{createElement:node},FormData:MockFormData,XMLHttpRequest:MockXHR,AbortController,crypto:{randomUUID:()=>require('node:crypto').randomUUID()},
  updateCommands(){},updateSessionListHeader(){},rememberSession(){},resetSourceView(){},resetAttachmentView(){},resetView(){},setConversationModel(){},setActiveSkill(){},renderSession(){},watchSession(){},setConn(){},restoreStatus(){},refreshSessions:async()=>{},
  snack:text=>notices.push(text),reportError:error=>notices.push(error.message),traceEv:()=>({className:'ev run',querySelector:()=>node()}),traceDetail(){},addManifest(manifest){runtime.committed.push(manifest)},committed:[],
  transcript:{addUser:view=>users.push(view),addTurn(){},finish:error=>notices.push(error)},renderInputRequest(){},consumeEventStream:async()=>{},inputAnswerText(){},runSlashCommand:assert.fail,
  api:async(url,options={})=>{apiCalls.push({url,method:options.method||'GET'});if(url==='/sessions'&&options.method==='POST')return runtime.createResponse?await runtime.createResponse:{session_id:'created-session',llm_model:'test'};const id=url.split('/')[2],files=runtime.pendingFilesBySession.get(id)||[];return {session_id:id,manifests:[],staged_manifests:files.map(file=>runtime.fileUploads.get(file).manifest).filter(Boolean),running:false}},
  fetch:async(url,options)=>{messages.push({url,body:JSON.parse(options.body)});return {ok:runtime.messageOK!==false,status:200,json:async()=>({error:'message failed'})}},
 };
 vm.runInNewContext(source.slice(source.indexOf('function hasImageInput('),source.indexOf('function setConversationModel(')),runtime);
 vm.runInNewContext(source.slice(source.indexOf('function autosize('),source.indexOf('async function api(')),runtime);
 vm.runInNewContext(source.slice(source.indexOf('function leaveView('),source.indexOf('function rememberSession(')),runtime);
 vm.runInNewContext(source.slice(source.indexOf('async function newSession('),source.indexOf('async function refreshSessions(')),runtime);
 vm.runInNewContext(source.slice(source.indexOf('async function resumeSession('),source.indexOf('function renderSession(')),runtime);
 vm.runInNewContext(source.slice(source.indexOf('const SIDECAR='),source.indexOf('function cleanSummary(')),runtime);
 vm.runInNewContext(source.slice(source.indexOf('async function upload('),source.indexOf('/* ---------- artifacts ---------- */')),runtime);
 vm.runInNewContext(source.slice(source.indexOf('async function send('),source.indexOf('const SKILL_NAMES=')),runtime);
 return {runtime,element,requests,messages,users,notices,apiCalls,chip:file=>element('#pendingFiles').children.find(chip=>chip.children[0].textContent===file.name)};
}
const attachmentFile=name=>({name,size:100,lastModified:1});
const attachmentManifest=(id,name,modality='eeg')=>({manifest_id:id,original_name:name,modality});
const attachmentTick=()=>new Promise(resolve=>setImmediate(resolve));

test('published shell is produced from the tested bridge sources',()=>{
 for(const name of ['app.js','app.css','record-card.css','index.html','execution-details.js','attachment-preview.js','file-mentions.js']){
  let expected=readFileSync(path.join(__dirname,'../frontend/bridge',name),'utf8');
  const localFonts=path.join(__dirname,'../frontend/bridge/fonts.local.css');
  if(name==='app.css'&&existsSync(localFonts))expected+='\n'+readFileSync(localFonts,'utf8');
  assert.equal(readFileSync(path.join(__dirname,'../gateway/internal/server/static',name),'utf8'),expected);
 }
});

test('selected workspace references reach the request and user message without uploading their files',async()=>{
 const {runtime:r,element,requests,messages,users}=attachmentRuntime();
 const refs=[{workspace_id:'ws_123456abcdef',path:'.',name:'当前文件夹',kind:'directory'}];
 r.fileMentions.references=()=>refs;element('#input').value='分析这个文件夹的 EEG';await r.send();
 assert.equal(requests.length,0);assert.deepEqual(messages[0].body.references,refs);assert.deepEqual(users[0].references,refs);
 assert.equal(messages[0].body.text,'分析这个文件夹的 EEG');
});

test('reference chips can be sent without duplicating paths in the message text or uploading files',async()=>{
 const {runtime:r,element,requests,messages,users}=attachmentRuntime();
 const refs=[{workspace_id:'ws_123456abcdef',path:'data_preprocessed_python/s01.dat',name:'s01.dat',kind:'file'},{workspace_id:'ws_123456abcdef',path:'face_video/s01/s01_trial01.avi',name:'s01_trial01.avi',kind:'file'}];
 let cleared;r.fileMentions.references=()=>refs;r.fileMentions.clear=sent=>{cleared=sent};
 r.autosize();assert.equal(element('#send').disabled,false);
 await r.send();
 assert.equal(requests.length,0);assert.equal(messages.length,1);
 assert.deepEqual(messages[0].body.references,refs);assert.deepEqual(users[0].references,refs);assert.deepEqual(cleared,refs);
 assert.equal(users[0].text,'分析引用的数据');assert.doesNotMatch(messages[0].body.text,/@|data_preprocessed_python|face_video/);
});

test('a reference-only follow-up remains sendable during an analysis and failures retain the reference',async()=>{
 const {runtime:r,element,messages}=attachmentRuntime();
 const refs=[{manifest_id:'vid_reference',name:'face.mp4',kind:'file'}];
 let cleared=false;r.fileMentions.references=()=>refs;r.fileMentions.clear=()=>{cleared=true};r.streaming=true;r.messageOK=false;
 r.autosize();assert.equal(element('#send').title,'发送补充说明');assert.equal(element('#send .ms').textContent,'arrow_upward');
 await r.send();
 assert.deepEqual(messages[0].body.references,refs);assert.deepEqual(messages[0].body.attachments,['vid_reference']);assert.equal(cleared,false);
});

test('restored prepared drafts submit their real IDs without uploading again',async()=>{
 const {runtime:r,element,requests,messages,chip}=attachmentRuntime();
 const manifest={...attachmentManifest('sig_saved','record.edf'),upload_group_id:'upl_abcdef123456'};
 r.restorePendingFiles({session_id:'session-one',staged_manifests:[manifest],previews:{}});r.renderPendingFiles();
 assert.equal(r.pendingFiles.length,1);assert.equal(chip(r.pendingFiles[0]).children[1].textContent,'已读入');
 element('#input').value='继续分析';await r.send();
 assert.equal(requests.length,0);assert.deepEqual(messages[0].body.attachments,['sig_saved']);
});

test('image drafts wait for an explicit capable model selection and retain the composer',async()=>{
 const {runtime:r,element,messages,users}=attachmentRuntime();
 r.modelCapabilities={vision:{image_input:true}};r.llmModels=['test','vision'];
 r.restorePendingFiles({session_id:'session-one',staged_manifests:[attachmentManifest('img_draft','脑电图.png','image')]});
 const draft='解释这张图的坐标与曲线';element('#input').value=draft;
 await r.send();
 assert.equal(messages.length,0);assert.equal(users.length,0);assert.equal(r.currentLLMModel,'test');
 assert.equal(element('#input').value,draft);assert.equal(r.pendingFiles.length,1);
 assert.equal(element('#llmPicker').open,true);assert.equal(r.submitting,false);assert.equal(r.streaming,false);
 r.currentLLMModel='vision';
 await r.send();
 assert.equal(messages.length,1);assert.deepEqual(messages[0].body.attachments,['img_draft']);
 assert.equal(messages[0].body.text,draft);assert.equal(r.pendingFiles.length,0);assert.equal(element('#input').value,'');
});

test('image follow-ups keep the current draft when a restored conversation uses a text model',async()=>{
 const {runtime:r,element,messages}=attachmentRuntime();
 r.manifests=[attachmentManifest('img_committed','图.png','image')];r.modelCapabilities={};
 element('#input').value='左下角是什么？';await r.send();
 assert.equal(messages.length,0);assert.equal(element('#input').value,'左下角是什么？');
 assert.equal(r.currentLLMModel,'test');assert.equal(element('#llmPicker').open,true);
});

test('a restored image conversation loads model capabilities before deciding whether to send',async()=>{
 const {runtime:r,element,messages}=attachmentRuntime();
 r.manifests=[attachmentManifest('img_restored','图.png','image')];r.llmModels=[];r.modelCapabilities={};
 let loaded=false;r.loadLLMModels=async()=>{loaded=true;r.llmModels=['test'];r.modelCapabilities={test:{image_input:true}}};
 element('#input').value='解释横坐标';await r.send();
 assert.equal(loaded,true);assert.equal(messages.length,1);assert.equal(r.currentLLMModel,'test');
 assert.equal(element('#llmPicker').open,undefined);
});

test('removing a restored draft deletes its server staging record',async()=>{
 const {runtime:r,apiCalls}=attachmentRuntime();
 r.restorePendingFiles({session_id:'session-one',staged_manifests:[attachmentManifest('doc_saved','paper.md','document')]});
 r.removePendingFile(r.pendingFiles[0]);await attachmentTick();
 assert.equal(r.pendingFiles.length,0);
 assert.ok(apiCalls.some(call=>call.method==='DELETE'&&call.url==='/sessions/session-one/attachments/doc_saved'));
});

test('one selected BrainVision group shares its transport namespace',async()=>{
 const {runtime:r,requests}=attachmentRuntime();
 const header=attachmentFile('record.vhdr'),data=attachmentFile('record.eeg');
 r.queueFiles([header,data]);await attachmentTick();
 const group=requests[0].body.fields.get('upload_group_id');assert.match(group,/^upl_[0-9a-f]{32}$/);
 requests[0].complete({sidecar:true});await attachmentTick();
 assert.equal(requests[1].body.fields.get('upload_group_id'),group);
 requests[1].complete({manifest:attachmentManifest('sig_group',header.name)});await attachmentTick();
});

test('a referenced sidecar with a different stem removes its ready header too',async()=>{
 const {runtime:r}=attachmentRuntime(),header=attachmentFile('record.vhdr'),data=attachmentFile('signal.eeg');
 r.pendingFiles=[header,data];
 r.fileUploads.set(header,{sessionId:'session-one',groupId:'upl_same',status:'ready',manifest:{...attachmentManifest('sig_group',header.name),source_files:['/uploads/record.vhdr','/uploads/signal.eeg']}});
 r.fileUploads.set(data,{sessionId:'session-one',groupId:'upl_same',status:'ready',manifest:null});
 r.removePendingFile(data);await attachmentTick();assert.equal(r.pendingFiles.length,0);
});

test('a header added later completes the existing sidecar group',async()=>{
 const {runtime:r,requests,messages,element}=attachmentRuntime(),data=attachmentFile('record.eeg'),header=attachmentFile('record.vhdr');
 r.queueFiles([data]);await attachmentTick();const group=requests[0].body.fields.get('upload_group_id');
 requests[0].complete({sidecar:true});await attachmentTick();
 element('#input').value='分析记录';await r.send();assert.equal(messages.length,0);
 r.queueFiles([header]);await attachmentTick();assert.equal(requests[1].body.fields.get('upload_group_id'),group);
 requests[1].complete({manifest:{...attachmentManifest('sig_complete',header.name),source_files:['/record.eeg','/record.vhdr']}});await attachmentTick();
 await r.send();assert.deepEqual(messages[0].body.attachments,['sig_complete']);
});

test('a completed record clears the missing-member failure of its earlier header',async()=>{
 const {runtime:r,requests,messages,element}=attachmentRuntime(),data=attachmentFile('record.eeg'),header=attachmentFile('record.vhdr');
 r.queueFiles([header]);await attachmentTick();const group=requests[0].body.fields.get('upload_group_id');
 requests[0].complete({error:'data file missing'},400);await attachmentTick();
 r.queueFiles([data]);await attachmentTick();assert.equal(requests[1].body.fields.get('upload_group_id'),group);
 requests[1].complete({manifest:{...attachmentManifest('sig_complete',header.name),source_files:['/record.eeg','/record.vhdr']}});await attachmentTick();
 assert.equal(r.fileUploads.get(header).status,'ready');assert.equal(r.fileUploads.get(data).manifest,null);
 element('#input').value='分析记录';await r.send();assert.deepEqual(messages[0].body.attachments,['sig_complete']);
});

test('removing an unfinished record does not remove another same-named upload group',()=>{
 const {runtime:r}=attachmentRuntime(),h1=attachmentFile('record.vhdr'),d1=attachmentFile('record.eeg'),h2=attachmentFile('record.vhdr'),d2=attachmentFile('record.eeg');
 r.pendingFiles=[h1,d1,h2,d2];for(const file of [h1,d1])r.fileUploads.set(file,{groupId:'upl_first',manifest:null});for(const file of [h2,d2])r.fileUploads.set(file,{groupId:'upl_second',manifest:null});
 r.removePendingFile(h1);assert.deepEqual(r.pendingFiles,[h2,d2]);
});

test('selecting EEG and video stages immediately while Enter waits for both and reuses their real IDs',async()=>{
 const {runtime:r,element,requests,messages,users,chip}=attachmentRuntime(),eeg=attachmentFile('record.edf'),video=attachmentFile('face.mp4');
 r.queueFiles([eeg,video]);await attachmentTick();
 assert.equal(requests.length,1);assert.equal(messages.length,0);assert.equal(r.uploading,true);
 assert.equal(requests[0].url,'/sessions/session-one/attachments');assert.equal(requests[0].body.fields.get('staged'),'true');
 assert.equal(element('#input').disabled,false);assert.equal(element('#send').disabled,false);
 assert.equal(chip(eeg).children[1].textContent,'上传中');
 requests[0].bytesSent();assert.equal(chip(eeg).children[1].textContent,'检查中');
 element('#input').value='分析脑电与视频';const sending=r.send();await attachmentTick();assert.equal(messages.length,0);
 requests[0].complete({manifest:attachmentManifest('real-eeg','record.edf')});await attachmentTick();
 assert.equal(requests.length,2);assert.equal(chip(eeg).children[1].textContent,'已读入');assert.equal(messages.length,0);
 element('#input').value='发送期间补写的下一条说明';
 requests[1].bytesSent();requests[1].complete({manifest:attachmentManifest('real-video','face.mp4','video')});await sending;
 assert.equal(requests.length,2);assert.equal(messages.length,1);assert.deepEqual(messages[0].body.attachments,['real-eeg','real-video']);
 assert.equal(users[0].attachments[0].id,'real-eeg');assert.equal(users[0].attachments[1].id,'real-video');
 assert.equal(r.pendingFiles.length,0);assert.equal(element('#pendingFiles').hidden,true);assert.equal(r.committed.length,2);
 assert.equal(element('#input').value,'发送期间补写的下一条说明');
});

test('failed uploads stay visible, require manual retry, and successful preparation survives a message failure',async()=>{
 const {runtime:r,element,requests,messages,notices,chip}=attachmentRuntime(),file=attachmentFile('record.edf');
 r.queueFiles([file]);await attachmentTick();requests[0].onerror();await attachmentTick();
 assert.equal(r.fileUploads.get(file).status,'error');assert.equal(r.pendingFiles.length,1);assert.equal(r.uploading,false);
 assert.equal(chip(file).children[1].textContent,'失败');assert.match(chip(file).children[1].title,/请检查连接后重试/);
 await r.send();assert.equal(messages.length,0);assert.equal(requests.length,1);assert.match(notices.at(-1),/点击附件上的重试/);
 const retry=chip(file).children.find(item=>item.className==='pending-file-retry');assert.equal(retry.disabled,false);retry.onclick();await attachmentTick();
 assert.equal(requests.length,2);requests[1].bytesSent();requests[1].complete({manifest:attachmentManifest('ready-after-retry',file.name)});await attachmentTick();
 assert.equal(r.fileUploads.get(file).status,'ready');assert.equal(r.pendingFiles.length,1);assert.equal(messages.length,0);
 r.messageOK=false;element('#input').value='读取数据';await r.send();
 assert.equal(r.pendingFiles.length,1);assert.equal(r.committed.length,0);assert.equal(requests.length,2);assert.equal(element('#input').value,'读取数据');
 r.messageOK=true;await r.send();assert.equal(requests.length,2);assert.equal(r.pendingFiles.length,0);assert.deepEqual(messages[1].body.attachments,['ready-after-retry']);
});

test('removing an in-flight file excludes its registered manifest without discarding the remaining video',async()=>{
 const {runtime:r,element,requests,messages,chip}=attachmentRuntime(),eeg=attachmentFile('record.edf'),video=attachmentFile('face.mp4');
 r.queueFiles([eeg,video]);await attachmentTick();chip(eeg).children.at(-1).onclick();
 assert.deepEqual(r.pendingFiles,[video]);element('#input').value='只分析视频';const sending=r.send();await attachmentTick();
 requests[0].complete({manifest:attachmentManifest('removed-eeg',eeg.name)});await attachmentTick();
 assert.equal(requests.length,2);assert.equal(messages.length,0);requests[1].complete({manifest:attachmentManifest('remaining-video',video.name,'video')});await sending;
 assert.deepEqual(messages[0].body.attachments,['remaining-video']);assert.equal(r.committed.length,1);
});

test('removing every file while Enter waits does not start an analysis',async()=>{
 const {runtime:r,requests,messages,chip}=attachmentRuntime(),file=attachmentFile('record.edf');
 r.queueFiles([file]);await attachmentTick();const sending=r.send();await attachmentTick();chip(file).children.at(-1).onclick();
 requests[0].complete({manifest:attachmentManifest('removed',file.name)});await sending;
 assert.equal(messages.length,0);assert.equal(r.pendingFiles.length,0);assert.equal(r.submitting,false);
});

test('BrainVision sidecars register before the header and removing a ready group never submits its header',async()=>{
 const {runtime:r,element,requests,messages,chip}=attachmentRuntime(),header=attachmentFile('record.vhdr'),data=attachmentFile('record.eeg'),markers=attachmentFile('record.vmrk'),video=attachmentFile('face.mp4');
 r.queueFiles([header,data,markers,video]);await attachmentTick();assert.equal(requests[0].body.fields.get('file'),data);
 requests[0].complete({sidecar:true,note:'data saved'});await attachmentTick();assert.equal(requests[1].body.fields.get('file'),markers);
 requests[1].complete({sidecar:true,note:'markers saved'});await attachmentTick();assert.equal(requests[2].body.fields.get('file'),header);
 requests[2].complete({manifest:attachmentManifest('prepared-header',header.name)});await attachmentTick();assert.equal(requests[3].body.fields.get('file'),video);
 assert.equal(chip(data).children[1].textContent,'附属文件已保存');assert.equal(chip(header).children[1].textContent,'已读入');
 chip(markers).children.at(-1).onclick();assert.deepEqual(r.pendingFiles,[video]);
 requests[3].complete({manifest:attachmentManifest('video-only',video.name,'video')});await attachmentTick();element('#input').value='分析保留的视频';await r.send();
 assert.deepEqual(messages[0].body.attachments,['video-only']);assert.equal(requests.length,4);
});

test('EEGLAB removal groups its data sidecar while a standalone EEG file remains independent',async()=>{
 const {runtime:r,requests,chip}=attachmentRuntime(),header=attachmentFile('record.set'),sidecar=attachmentFile('record.fdt'),standalone=attachmentFile('nihon.eeg');
 r.queueFiles([header,sidecar,standalone]);await attachmentTick();assert.equal(requests[0].body.fields.get('file'),sidecar);
 chip(sidecar).children.at(-1).onclick();assert.deepEqual(r.pendingFiles,[standalone]);
 requests[0].complete({sidecar:true});await attachmentTick();assert.equal(requests[1].body.fields.get('file'),standalone);
 requests[1].complete({manifest:attachmentManifest('nihon',standalone.name)});await attachmentTick();
 assert.equal(chip(standalone).children[1].textContent,'已读入');
 chip(standalone).children.at(-1).onclick();assert.equal(r.pendingFiles.length,0);assert.equal(requests.length,2);
});

test('selection before session creation binds to the created session and prepared files restore only to their owner',async()=>{
 const {runtime:r,element,requests,messages,apiCalls}=attachmentRuntime(null),first=attachmentFile('first.edf'),second=attachmentFile('second.edf');
 let created;r.createResponse=new Promise(resolve=>created=resolve);r.queueFiles([first]);await attachmentTick();
 assert.equal(r.navigating,true);assert.equal(requests.length,0);await r.newSession();await r.resumeSession('unwanted-session');
 assert.equal(apiCalls.length,1);created({session_id:'created-session',llm_model:'test'});await attachmentTick();
 assert.equal(requests[0].url,'/sessions/created-session/attachments');assert.equal(r.fileUploads.get(first).sessionId,'created-session');
 await r.resumeSession('session-two');await r.newSession();assert.equal(r.sessionId,'created-session');assert.equal(apiCalls.length,1);
 requests[0].complete({manifest:attachmentManifest('first-ready',first.name)});await attachmentTick();await r.resumeSession('session-two');
 assert.equal(r.pendingFiles.length,0);r.queueFiles([second]);await attachmentTick();assert.equal(requests[1].url,'/sessions/session-two/attachments');
 requests[1].complete({manifest:attachmentManifest('second-ready',second.name)});await attachmentTick();element('#input').value='第二会话';await r.send();
 assert.equal(messages[0].url,'/sessions/session-two/messages');assert.deepEqual(messages[0].body.attachments,['second-ready']);
 await r.resumeSession('created-session');assert.equal(r.pendingFiles.length,1);assert.equal(r.pendingFiles[0],first);element('#input').value='第一会话';await r.send();
 assert.equal(requests.length,2);assert.equal(messages[1].url,'/sessions/created-session/messages');assert.deepEqual(messages[1].body.attachments,['first-ready']);
});

test('Enter during initial session creation waits for preparation and retains text typed during creation',async()=>{
 const {runtime:r,element,requests,messages}=attachmentRuntime(null),file=attachmentFile('initial.edf');
 let created;r.createResponse=new Promise(resolve=>created=resolve);r.queueFiles([file]);await attachmentTick();
 assert.equal(element('#input').disabled,false);assert.equal(element('#send').disabled,false);
 element('#input').value='会话创建期间输入的目标';const sending=r.send();await attachmentTick();assert.equal(messages.length,0);
 created({session_id:'initial-session',llm_model:'test'});await attachmentTick();
 assert.equal(element('#input').value,'会话创建期间输入的目标');assert.equal(requests[0].url,'/sessions/initial-session/attachments');
 requests[0].complete({manifest:attachmentManifest('initial-ready',file.name)});await sending;
 assert.equal(messages.length,1);assert.equal(messages[0].body.text,'会话创建期间输入的目标');assert.deepEqual(messages[0].body.attachments,['initial-ready']);
});

test('a failed initial session creation leaves a visible file retry without issuing an attachment request',async()=>{
 const {runtime:r,requests,chip}=attachmentRuntime(null),file=attachmentFile('initial.edf');
 r.createResponse=Promise.reject(new Error('session creation failed'));r.queueFiles([file]);await attachmentTick();
 assert.equal(requests.length,0);assert.equal(r.navigating,false);assert.equal(r.fileUploads.get(file).status,'error');
 assert.equal(chip(file).children[1].title,'session creation failed');
 r.createResponse=Promise.resolve({session_id:'retried-session',llm_model:'test'});chip(file).children.find(item=>item.className==='pending-file-retry').onclick();await attachmentTick();
 assert.equal(requests[0].url,'/sessions/retried-session/attachments');requests[0].complete({manifest:attachmentManifest('retried-initial',file.name)});await attachmentTick();
 assert.equal(r.fileUploads.get(file).status,'ready');assert.equal(r.pendingFiles.length,1);
});


test('report download paths keep readable labels and never rewrite IDs inside Markdown links',()=>{
 const html=context.md('产物：[审阅记录](/artifacts/art_abcdef/file)、[数据表](/artifacts/art_123456/file?download=1)。');
 assert.match(html,/data-art="art_abcdef">审阅记录/);
 assert.match(html,/data-art="art_123456">数据表/);
 assert.doesNotMatch(html,/\[审阅记录\]|\/artifacts\/查看产物/);
});
