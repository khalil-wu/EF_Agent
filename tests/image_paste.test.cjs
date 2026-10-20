const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {File}=require('node:buffer');

function composer(){
  const source=fs.readFileSync(require.resolve('../frontend/bridge/app.js'),'utf8');
  const block=source.match(/\$\('#input'\)\.addEventListener\('paste',event=>\{[\s\S]*?\n\}\);/)[0];
  let handler,queued=[];
  const input={value:'说明：',selectionStart:3,selectionEnd:3,addEventListener(type,fn){handler=fn},setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end)}};
  vm.runInNewContext(block,{$:()=>input,File,queueFiles:files=>queued.push(...files)});
  return {input,paste:handler,files:()=>queued};
}

test('normal text paste keeps the browser text behavior',()=>{
  const view=composer();let prevented=false;
  view.paste({clipboardData:{items:[{kind:'string',type:'text/plain'}],getData:()=> '正文'},preventDefault(){prevented=true}});
  assert.equal(prevented,false);assert.equal(view.files().length,0);
});

test('image paste queues a real File and preserves accompanying text at the cursor',async()=>{
  const view=composer(),picture=new File([new Uint8Array([137,80,78,71])],'image.png',{type:'image/png'});let prevented=false;
  view.paste({clipboardData:{items:[{kind:'file',type:'image/png',getAsFile:()=>picture}],getData:()=> '请解释这张图'},preventDefault(){prevented=true}});
  assert.equal(prevented,true);assert.equal(view.input.value,'说明：请解释这张图');
  const uploaded=view.files()[0];assert.equal(uploaded.type,'image/png');assert.equal(uploaded.size,picture.size);assert.match(uploaded.name,/^粘贴图片-.+\.png$/);
  assert.deepEqual(new Uint8Array(await uploaded.arrayBuffer()),new Uint8Array(await picture.arrayBuffer()));
});
