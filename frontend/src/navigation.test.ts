import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../bridge/app.js',import.meta.url),'utf8');
const leaveView=source.match(/^function leaveView\(\).+$/m)![0];
const resumeSession=source.slice(source.indexOf('async function resumeSession('),source.indexOf('function renderSession('));
const globalKeyboardListeners=[...source.matchAll(/^document\.addEventListener\('keydown',.+$/gm)].map(match=>match[0]).join('\n');

function deferred<T>(){
  let resolve!:(value:T)=>void;
  const promise=new Promise<T>(finish=>resolve=finish);
  return {promise,resolve};
}

function navigation(){
  const requests=new Map<string,ReturnType<typeof deferred<any>>>();
  const rendered:string[]=[],restored:string[]=[],remembered:string[]=[],watched:{id:string;version:number}[]=[],saved:string[]=[],messages:string[]=[];
  let aborted=0;
  const context=vm.createContext({
    sessionId:'old',viewVersion:0,streaming:true,submitting:false,uploading:false,navigating:false,
    uploadTask:null,streamController:{abort(){aborted++}},stopRequested:false,followBottom:false,
    pendingFiles:[],pendingFilesBySession:new Map(),
    fileMentions:{save:(id:string)=>saved.push(id),restore:(id:string)=>restored.push(id)},
    api:(url:string)=>{const task=deferred<any>();requests.set(url,task);return task.promise},
    resetSourceView(){},resetAttachmentView(){},autosize(){},restorePendingFiles(){},renderPendingFiles(){},
    renderSession:(snapshot:any)=>rendered.push(snapshot.session_id),
    rememberSession:(id:string)=>remembered.push(id),
    watchSession:(id:string,version:number)=>watched.push({id,version}),
    refreshSessions:async()=>{},reportError(){},snack:(message:string)=>messages.push(message),
  });
  vm.runInContext(leaveView+'\n'+resumeSession,context);
  return {context,requests,rendered,restored,remembered,watched,saved,messages,aborted:()=>aborted};
}

test('a newer session selection wins when the older request finishes first',async()=>{
  const f=navigation();
  const first=f.context.resumeSession('first'),last=f.context.resumeSession('last');
  assert.deepEqual([...f.requests.keys()],['/sessions/first','/sessions/last']);
  assert.equal(f.aborted(),1,'changing views detaches the old stream without starting a stop request');
  f.requests.get('/sessions/first')!.resolve({session_id:'first',running:true});
  await first;
  assert.deepEqual(f.rendered,[],'an obsolete response must not render an intermediate session');
  assert.equal(f.context.navigating,true,'the newer request still owns the loading state');
  f.requests.get('/sessions/last')!.resolve({session_id:'last',running:true});
  await last;
  assert.equal(f.context.sessionId,'last');
  assert.equal(f.context.navigating,false);
  assert.deepEqual(f.rendered,['last']);
  assert.deepEqual(f.restored,['last']);
  assert.deepEqual(f.remembered,['last']);
  assert.deepEqual(f.watched,[{id:'last',version:2}]);
  assert.deepEqual(f.messages,[],'navigation alone must not show the file-submission warning');
});

test('a late older response cannot replace the already rendered newest session or start its stream',async()=>{
  const f=navigation();
  const first=f.context.resumeSession('first'),last=f.context.resumeSession('last');
  f.requests.get('/sessions/last')!.resolve({session_id:'last',running:false});
  await last;
  f.requests.get('/sessions/first')!.resolve({session_id:'first',running:true});
  await first;
  assert.equal(f.context.sessionId,'last');
  assert.equal(f.context.navigating,false);
  assert.deepEqual(f.rendered,['last']);
  assert.deepEqual(f.restored,['last']);
  assert.deepEqual(f.watched,[],'the obsolete running session must not attach a reader over the selected view');
});

test('an active file upload retains its session instead of redirecting the pending file registration',async()=>{
  const f=navigation();
  f.context.uploading=true;
  await f.context.resumeSession('other');
  assert.equal(f.context.sessionId,'old');
  assert.equal(f.context.viewVersion,0);
  assert.equal(f.requests.size,0);
  assert.equal(f.aborted(),0);
  assert.deepEqual(f.saved,[]);
  assert.equal(f.messages.length,1);
});

function keyboard(){
  const classes=new Set(['sheet-open']),drawerClasses=new Set(['open']),closed:string[]=[],listeners:((event:any)=>void)[]=[];
  const dialog={open:false},lightbox={style:{display:'flex'}},drawer={classList:{contains:(name:string)=>drawerClasses.has(name)}};
  let popover=true;
  const elements:Record<string,any>={'#sessionDialog':dialog,'#lightbox':lightbox,'#drawer':drawer};
  const document={
    body:{classList:{contains:(name:string)=>classes.has(name)}},
    querySelector:(selector:string)=>selector===':popover-open'&&popover?{open:true}:null,
    addEventListener:(type:string,listener:(event:any)=>void)=>{if(type==='keydown')listeners.push(listener)},
  };
  const context=vm.createContext({
    document,$:(selector:string)=>elements[selector],narrow:()=>true,
    closeDrawer:()=>{closed.push('drawer');drawerClasses.delete('open')},
    closeSheet:()=>{closed.push('sheet');classes.delete('sheet-open')},
    newSession:async()=>{},openDrawer(){},reportError(){},
  });
  vm.runInContext(globalKeyboardListeners,context);
  function press(defaultPrevented=false){
    const event={key:'Escape',ctrlKey:false,metaKey:false,defaultPrevented,preventDefault(){this.defaultPrevented=true}};
    listeners.forEach(listener=>listener(event));
  }
  return {classes,drawerClasses,closed,dialog,lightbox,press,dismissPopover(){popover=false}};
}

test('Escape leaves the drawer and result sheet intact while a popover is the top layer',()=>{
  const f=keyboard();
  f.press();
  assert.deepEqual(f.closed,[],'the browser or popover owns this Escape before the shell handles lower layers');
  assert.equal(f.drawerClasses.has('open'),true);
  assert.equal(f.classes.has('sheet-open'),true);
  assert.equal(f.lightbox.style.display,'flex');
});

test('subsequent Escape presses close only lightbox, then result sheet, then drawer',()=>{
  const f=keyboard();
  f.dismissPopover();
  f.press();
  assert.equal(f.lightbox.style.display,'none');
  assert.deepEqual(f.closed,[]);
  f.press();
  assert.deepEqual(f.closed,['sheet']);
  assert.equal(f.drawerClasses.has('open'),true);
  f.press();
  assert.deepEqual(f.closed,['sheet','drawer']);
});

test('Escape consumed by a local picker or an open dialog does not dismiss lower shell surfaces',()=>{
  const f=keyboard();
  f.dismissPopover();
  f.press(true);
  assert.equal(f.lightbox.style.display,'flex');
  assert.deepEqual(f.closed,[]);
  f.dialog.open=true;
  f.press();
  assert.equal(f.lightbox.style.display,'flex');
  assert.deepEqual(f.closed,[]);
});
