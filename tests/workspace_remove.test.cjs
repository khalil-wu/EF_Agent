const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('../frontend/node_modules/jsdom');
const source=readFileSync(path.join(__dirname,'../frontend/bridge/app.js'),'utf8');
const record={workspace_id:'ws_abcdef123456',name:'采集数据',root:'E:/recordings'};

function fixture(failure){
  const {window}=new JSDOM('<input id="sessionSearch"><div id="sesList"></div><div id="workspaceMenu"><div id="workspaceMenuTitle"></div><button id="removeWorkspace"></button><p id="workspaceMenuNote"></p></div>',{url:'http://localhost/'});
  const document=window.document,calls=[],notices=[],cleared=[];
  document.querySelector('#workspaceMenu').showPopover=function(){this.dataset.open='true'};
  const storage=new Map();
  const runtime={document,$:selector=>document.querySelector(selector),innerWidth:1280,innerHeight:900,
    IconSystem:{svg:()=>''},esc:String,localStorage:{setItem:(key,value)=>storage.set(key,value)},
    sessionId:'ses_current',currentWorkspaceID:record.workspace_id,streaming:false,uploading:false,submitting:false,navigating:false,
    workspaceCatalog:[record],sessionsCache:[{session_id:'ses_current',workspace_id:record.workspace_id,title:'保留的分析'}],expandedWorkspaces:{[record.workspace_id]:true},trashView:false,
    fileMentions:{removeWorkspace:id=>cleared.push(id),update(){}},resetAttachmentView(){},snack:text=>notices.push(text),
    closeDrawer(){},resumeSession(){},openSessionMenu(){},newSession(){},reportError(){},
    api:async(url,options)=>{calls.push([url,options.method]);if(failure)throw failure;return {detached_sessions:1}},
    refreshSessions:async()=>{runtime.workspaceCatalog=[];runtime.sessionsCache[0].workspace_id='';runtime.renderSessionList()}
  };
  vm.runInNewContext(source.slice(source.indexOf('function renderSessionList('),source.indexOf('function openSessionMenu(')),runtime);
  return {runtime,document,calls,notices,cleared,storage};
}

test('workspace menu removes the mount and retains its conversation as independent',async()=>{
  const f=fixture();f.runtime.renderSessionList();
  f.document.querySelector('.workspace-more').click();
  assert.equal(f.document.querySelector('#workspaceMenu').dataset.open,'true');
  assert.match(f.document.querySelector('#workspaceMenuNote').textContent,/保留本地文件/);
  await f.runtime.removeWorkspace(record);
  assert.deepEqual(f.calls,[[`/workspaces/${record.workspace_id}`,'DELETE']]);
  assert.equal(f.runtime.currentWorkspaceID,'');
  assert.equal(f.document.querySelector('.workspace-group'),null);
  assert.match(f.document.querySelector('#sesList').textContent,/独立会话.*保留的分析/);
  assert.deepEqual(f.cleared,[record.workspace_id]);
  assert.equal(f.storage.get('eeg-workspace-folders'),'{}');
});

test('a failed removal keeps the current workspace and draft references',async()=>{
  const failure=new Error('工作区正在分析中');const f=fixture(failure);
  await assert.rejects(f.runtime.removeWorkspace(record),error=>error===failure);
  assert.equal(f.runtime.currentWorkspaceID,record.workspace_id);
  assert.equal(f.runtime.workspaceCatalog[0],record);
  assert.deepEqual(f.cleared,[]);assert.deepEqual(f.notices,[]);
});

test('workspace removal is unavailable while one of its conversations is running',()=>{
  const f=fixture();f.runtime.sessionsCache[0].running=true;f.runtime.renderSessionList();
  f.document.querySelector('.workspace-more').click();
  assert.equal(f.document.querySelector('#removeWorkspace').disabled,true);
  assert.match(f.document.querySelector('#workspaceMenuNote').textContent,/完成后可移除/);
});
