const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=readFileSync(path.join(__dirname,'../frontend/bridge/workspace-mount.js'),'utf8');
const WorkspaceMount=vm.runInNewContext(source+'\nWorkspaceMount');

test('native folder selection mounts the original workspace and opens its session',async()=>{
  const calls=[];
  const picker=WorkspaceMount.mount({
    api:async(url,request)=>{
      calls.push({url,method:request.method,body:request.body,contentType:request.headers['Content-Type']});
      return {workspace_id:'ws_selected',root:'E:/采集数据'};
    },
    select:async id=>calls.push({workspace:id})
  });
  await picker.openMount();
  assert.deepEqual(calls,[
    {url:'/workspaces/pick',method:'POST',body:'{}',contentType:'application/json'},
    {workspace:'ws_selected'}
  ]);
});

test('cancelling the folder dialog leaves the current session unchanged',async()=>{
  let selected=false;
  await WorkspaceMount.mount({api:async()=>({cancelled:true}),select:()=>{selected=true}}).openMount();
  assert.equal(selected,false);
});

test('native picker failures reach the existing error presentation',async()=>{
  const failure=new Error('系统文件夹选择器启动失败');
  await assert.rejects(WorkspaceMount.mount({api:async()=>{throw failure},select:()=>assert.fail('selection after failure')}).openMount(),error=>error===failure);
});

test('the workspace button recovers after selection, cancellation and failure',async()=>{
  const app=readFileSync(path.join(__dirname,'../frontend/bridge/app.js'),'utf8');
  const start=app.indexOf("$('#mountWorkspace').onclick=async()=>{");
  const handler=app.slice(start,app.indexOf('\n};',start)+3);
  for(const failure of [null,new Error('picker failed')]){
    const attributes=new Map();
    const button={disabled:false,setAttribute:(key,value)=>attributes.set(key,value),removeAttribute:key=>attributes.delete(key)};
    const errors=[];
    const runtime={$:()=>button,reportError:error=>errors.push(error),workspaceMount:{openMount:async()=>{
      assert.equal(button.disabled,true);
      assert.equal(attributes.get('aria-busy'),'true');
      if(failure)throw failure;
    }}};
    vm.runInNewContext(handler,runtime);
    await button.onclick();
    assert.equal(button.disabled,false);
    assert.equal(attributes.has('aria-busy'),false);
    assert.equal(button.title,'选择本地文件夹');
    assert.deepEqual(errors,failure?[failure]:[]);
  }
});
