import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {JSDOM} from 'jsdom';
import * as catalog from './result-catalog';

test('different export sources and formats stay distinct, repeated CSV serialization shares one catalog entry',()=>{
  const records:any={features:{artifact_id:'features',kind:'features'},csv:{artifact_id:'csv',kind:'table',summary:{source:'features',format:'csv'},provenance:{created_by:'export@1'}},again:{artifact_id:'again',kind:'table',summary:{source:'csv',format:'csv'},provenance:{created_by:'export@1'}},json:{artifact_id:'json',kind:'table',summary:{source:'csv',format:'json'},provenance:{created_by:'export@1'}},other:{artifact_id:'other',kind:'table',summary:{source:'other-features',format:'csv'},provenance:{created_by:'export@1'}}};
  const items=catalog.resultItems(['csv','again','json','other'],id=>records[id]);
  assert.deepEqual(items.map(item=>item.artifact_id),['csv','json','other']);
  assert.equal(catalog.resultSummary(items),'3 份表');
  assert.equal(catalog.isProcessResult({kind:'normalized_signal'}),false,'requested cleaned EEG remains a deliverable');
  assert.equal(catalog.resultTitle({kind:'plot',summary:{kind:'clean_ica_iclabel'}}),'ICA 成分头皮分布');
});

test('a workspace delivery keeps the reported total rather than claiming the eight preview paths are complete',()=>{
  const files=Array.from({length:8},(_,index)=>({workspace_id:'ws',path:`analysis/trial_${index+1}.npy`,kind:'file'}));
  const deliveries=catalog.workspaceDeliveries({entries:[{type:'tool',id:'batch',result:{workspace_files:files,workspace_files_count:41}}]});
  assert.equal(deliveries[0].count,41);
  assert.equal(deliveries[0].files.length,8);
});

test('analysis ranges use actual methods and inputs instead of a generic follow-up request',()=>{
  const records:any={asr:{artifact_id:'asr',kind:'normalized_signal',source_manifest_ids:['sig'],summary:{method:'asr'}},ica:{artifact_id:'ica',kind:'plot',source_manifest_ids:['sig'],summary:{kind:'clean_ica_iclabel'}}};
  assert.equal(catalog.resultGroupTitle({entries:[],artifacts:['asr','ica']},id=>records[id],()=> 's02.dat'),'ICA / ASR 方法对照 · s02.dat');
  const files=[{workspace_id:'ws',path:'analysis/results.csv',name:'results.csv',artifact_id:'asr'}];
  const deliveries=catalog.workspaceDeliveries({entries:[{type:'tool',id:'write',result:{workspace_files:files}},{type:'tool',id:'edit',result:{workspace_file:files[0]}}]});
  assert.equal(deliveries.length,1);assert.equal(deliveries[0].count,1);assert.equal(deliveries[0].files[0].artifact_id,'asr');
});

test('result browser scopes an analysis, searches Chinese names, and renders only the selected artifact',()=>{
  const dom=new JSDOM('<div class="sheet-body"></div><section id="resultCatalog"><button id="resultScope"><span id="resultScopeLabel"></span></button><div id="resultScopeMenu"></div><input id="resultSearch"><div id="resultList"></div></section><p id="noArt"></p><section id="resultDetail" hidden><div id="artifacts"></div></section>');
  const records:any={ica:{artifact_id:'ica',kind:'plot',source_manifest_ids:['sig'],summary:{kind:'clean_ica_iclabel',available_formats:['png','svg']}},psd:{artifact_id:'psd',kind:'plot',source_manifest_ids:['sig'],summary:{kind:'psd'}}};
  for(let trial=1;trial<=40;trial++)records['signal'+trial]={artifact_id:'signal'+trial,kind:'normalized_signal',summary:{method:'asr'},uri:'signal.npy'};
  const rows=[{type:'user',text:'检查 ICA 成分'},{type:'turn',id:'ica-turn',entries:[],artifacts:['ica']},{type:'user',text:'清洗全部40个试次'},{type:'turn',id:'batch-turn',entries:[],artifacts:Object.keys(records).filter(id=>id.startsWith('signal'))}];
  const context:any={document:dom.window.document,$:(selector:string)=>dom.window.document.querySelector(selector),TranscriptUI:catalog,artifacts:records,manifests:[{m:{manifest_id:'sig',original_name:'s02.dat'}}],transcript:{getSnapshot:()=>({rows})},resultScope:'latest',selectedArtifact:null,KIND_ICON:{plot:'image'},artifactTitle:catalog.resultTitle,artifactTimeLabel:()=>'',artifactSourceOverview:()=>'',esc:(value:any)=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!)),showTab(){},openSheet(){},reportError(){},api:async()=>{},lightbox(){}};
  vm.createContext(context);
  const source=readFileSync(new URL('../bridge/app.js',import.meta.url),'utf8');
  vm.runInContext(source.slice(source.indexOf('function resultGroups('),source.indexOf('function updateAttachments(')),context);
  vm.runInContext(source.slice(source.indexOf('function renderArtifactDetail('),source.indexOf("document.addEventListener('click'",source.indexOf('function renderArtifactDetail('))),context);
  assert.equal(context.workspaceDeliveryFolder([{workspace_id:'ws',path:'analysis/clean/trial_01.npy'}],41).path,'.','a partial preview cannot establish the parent folder of unlisted files');
  context.renderResultCatalog();
  assert.equal(dom.window.document.querySelectorAll('.result-row').length,40);
  assert.match(dom.window.document.querySelector('.signal-results summary')!.textContent!,/40/);
  assert.equal(dom.window.document.querySelectorAll('img').length,0,'the directory does not render dozens of full previews');
  context.openResults('ica-turn');
  assert.equal(dom.window.document.querySelectorAll('.result-row').length,1);
  dom.window.document.querySelector<HTMLButtonElement>('.result-row')!.click();
  assert.equal(dom.window.document.querySelectorAll('.artcard').length,1);
  assert.match(dom.window.document.querySelector('#artifacts')!.textContent!,/ICA 成分头皮分布/);
  assert.match(dom.window.document.querySelector('.artifact-description')!.textContent!,/头皮空间权重/);
  context.openArtifact('psd');
  assert.equal(dom.window.document.querySelectorAll('.artcard').length,1);
  assert.equal(dom.window.document.querySelector('img')!.getAttribute('src'),'/artifacts/psd/file');
  context.openResults();context.resultScope='all';context.$('#resultSearch').value='ICA';context.renderResultCatalog();
  assert.equal(dom.window.document.querySelectorAll('.result-row').length,1);
  assert.match(dom.window.document.querySelector('.result-row')!.textContent!,/ICA/);
  const files=Array.from({length:41},(_,index)=>({workspace_id:'ws',path:index===40?'analysis/s02_clean_summary.csv':`analysis/s02_clean/trial_${index+1}.npy`,name:index===40?'s02_clean_summary.csv':`trial_${index+1}.npy`,kind:'file',...(index===0?{artifact_id:'signal1'}:{})}));
  rows.push({type:'user',text:'继续'} as any,{type:'turn',id:'saved-batch',artifacts:['signal1'],entries:[{type:'tool',id:'save-all',result:{workspace_files:files}}]} as any);
  context.openResults('saved-batch');
  assert.equal(dom.window.document.querySelectorAll('.saved-file-row').length,41);
  assert.equal(dom.window.document.querySelectorAll('.result-row[data-result-id="signal1"]').length,0,'a saved artifact has one primary file entry');
  assert.equal(dom.window.document.querySelectorAll('#resultScopeMenu [value="saved-batch"]').length,1);
  assert.doesNotMatch(dom.window.document.querySelector('#resultScopeMenu')!.textContent!,/继续/);
  context.$('#resultSearch').value='s02_clean_summary';context.renderResultCatalog();
  assert.equal(dom.window.document.querySelectorAll('.saved-file-row').length,1);
  const download=dom.window.document.querySelector<HTMLAnchorElement>('.saved-file-row a')!;
  assert.equal(download.getAttribute('href'),'/workspaces/ws/file?path=analysis%2Fs02_clean_summary.csv&download=1');
  dom.window.close();
});
