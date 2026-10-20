import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import {JSDOM} from 'jsdom';

const source=readFileSync(new URL('../bridge/file-mentions.js',import.meta.url),'utf8');
const icons=createRequire(import.meta.url)('../../gateway/internal/server/static/icons.js');
const plain=(value:any)=>JSON.parse(JSON.stringify(value));
const pause=()=>new Promise(resolve=>setTimeout(resolve,125));
function fixture(list:(query:string)=>Promise<any[]>){
  const dom=new JSDOM('<textarea id="input"></textarea><div id="menu" hidden></div><div id="chips" hidden></div>');
  dom.window.HTMLElement.prototype.scrollIntoView=function(){};
  const document=dom.window.document,input=document.querySelector('textarea')!,menu=document.querySelector('#menu')!,chips=document.querySelector('#chips')!;
  const sandbox:any={module:{exports:{}},IconSystem:icons,setTimeout,clearTimeout};vm.runInNewContext(source,sandbox);
  const api=sandbox.module.exports;
  const picker=api.mount({input,menu,chips,list,scope:()=> 'workspace',preview(){},changed(){picker.update()}});
  async function type(text:string){input.value=text;input.setSelectionRange(text.length,text.length);input.focus();picker.update();await pause()}
  function key(key:string){return picker.handleKey({key,preventDefault(){},stopPropagation(){}})}
  return {api,picker,input,menu,chips,type,key};
}
const file=(path:string)=>({workspace_id:'ws_123456abcdef',name:path.split('/').at(-1),path,kind:'file'});

test('mention parsing supports root, quoted Chinese paths and caret position without treating email as a reference',()=>{
  const {api}=fixture(async()=>[]);
  assert.equal(api.queryAt('联系 a@b.com',10),null);
  assert.equal(api.queryAt('分析 @.',5).query,'.');
  assert.equal(api.queryAt('分析 @"被试 01/',11).query,'被试 01/');
});

test('choosing a same-name file preserves its complete path; drafts and removing the reference keep scope correct',async()=>{
  const f=fixture(async()=>[file('甲/record.npz'),file('乙/record.npz')]);
  await f.type('分析 @record');f.key('ArrowDown');f.key('Enter');
  assert.equal(f.input.value,'分析 ');
  assert.equal(f.picker.references()[0].path,'乙/record.npz');
  assert.match(f.chips.textContent!,/record.npz/);
  assert.equal(f.chips.querySelector('[data-reference-preview]')!.getAttribute('title'),'预览 乙/record.npz');
  f.picker.save('one');f.picker.restore('two');assert.equal(f.input.value,'');assert.equal(f.picker.references().length,0);
  f.picker.restore('one');assert.equal(f.picker.references()[0].path,'乙/record.npz');
  f.chips.querySelector<HTMLButtonElement>('[data-reference-remove]')!.click();assert.equal(f.picker.references().length,0);assert.doesNotMatch(f.input.value,/@乙/);
  await f.type('@.');f.key('Escape');assert.equal((f.menu as HTMLElement).hidden,true);
});

test('folder navigation and root selection use directory references, not a list of partially loaded files',async()=>{
  const queries:string[]=[];
  const f=fixture(async q=>{queries.push(q);return q==='被试 01/'?[file('被试 01/a.npz')]:[{workspace_id:'ws_123456abcdef',path:'被试 01',name:'被试 01',kind:'directory'}]});
  await f.type('@被试');f.key('ArrowRight');await pause();assert.equal(queries.at(-1),'被试 01/');f.key('Enter');
  assert.equal(f.input.value,'');assert.equal(f.picker.references()[0].path,'被试 01/a.npz');
  const root=fixture(async()=>[{workspace_id:'ws_123456abcdef',path:'.',name:'recordings',kind:'directory'}]);
  await root.type('@.');root.key('Enter');assert.equal(root.input.value,'');assert.equal(root.picker.references()[0].kind,'directory');
  root.input.value='分析整个文件夹';root.picker.update();assert.equal(root.picker.references().length,1);
});

test('removing a workspace drops its references from every draft while preserving their text',async()=>{
  const f=fixture(async()=>[file('a.npz')]);
  f.picker.restore('first');await f.type('分析 @a');f.key('Enter');f.picker.save('first');
  const firstText=f.input.value;
  f.picker.restore('second');await f.type('继续 @a');f.key('Enter');f.picker.save('second');
  const secondText=f.input.value;
  f.picker.removeWorkspace('ws_123456abcdef');
  assert.equal(f.picker.references().length,0);assert.equal(f.input.value,secondText);
  f.picker.restore('first');assert.equal(f.picker.references().length,0);assert.equal(f.input.value,firstText);
  f.picker.restore('second');assert.equal(f.picker.references().length,0);assert.equal(f.input.value,secondText);
});

test('late searches cannot replace a newer query or reopen a dismissed picker',async()=>{
  let finish:(items:any[])=>void=()=>{};
  const f=fixture(q=>q==='a'?new Promise(resolve=>finish=resolve):Promise.resolve([file('b.npz')]));
  await f.type('@a');await f.type('@b');finish([file('a.npz')]);await Promise.resolve();
  assert.match(f.menu.textContent!,/b.npz/);assert.doesNotMatch(f.menu.textContent!,/a.npz/);
  await f.type('@a');f.key('Escape');finish([file('a.npz')]);await Promise.resolve();assert.equal((f.menu as HTMLElement).hidden,true);
});

test('replacing a reference at the caret consumes the old filename suffix and preserves the following task',async()=>{
  const f=fixture(async()=>[file('乙/record.npz')]);
  f.input.value='分析 @old.npz 继续分析';f.input.setSelectionRange(6,6);f.input.focus();f.picker.update();await pause();f.key('Enter');
  assert.equal(f.input.value,'分析 继续分析');
  assert.equal(f.picker.references()[0].path,'乙/record.npz');
});

test('multiple reference chips keep their full paths while editing text independently',async()=>{
  const eeg=file('data_preprocessed_python/data_preprocessed_python/s01.dat'),video=file('face_video/s01/s01_trial01.avi');
  const f=fixture(async query=>query.includes('avi')?[video]:[eeg]);
  await f.type('@s01.dat');f.key('Enter');
  await f.type('@s01_trial01.avi');f.key('Enter');
  assert.equal(f.input.value,'');assert.equal(f.picker.references().length,2);
  assert.deepEqual(plain(f.picker.references()),[eeg,video]);
  await f.type('结合脑电和视频分析情绪');
  assert.equal(f.picker.references().length,2);
  f.chips.querySelector<HTMLButtonElement>('[data-reference-remove="0"]')!.click();
  assert.equal(f.input.value,'结合脑电和视频分析情绪');
  assert.deepEqual(plain(f.picker.references()),[video]);
});

test('choosing a reference again does not duplicate it and submitting clears only the sent references',async()=>{
  const first=file('a.npz'),second=file('b.npz');
  const f=fixture(async query=>[query==='b'?second:first]);
  await f.type('@a');f.key('Enter');const submitted=f.picker.references();
  await f.type('@a');f.key('Enter');assert.equal(f.picker.references().length,1);
  await f.type('@b');f.key('Enter');
  f.picker.clear(submitted);
  assert.deepEqual(plain(f.picker.references()),[second]);
  assert.equal(f.chips.querySelectorAll('[data-reference-preview]').length,1);
  f.picker.clear();assert.equal(f.picker.references().length,0);assert.equal((f.chips as HTMLElement).hidden,true);
});
