const {test}=require('node:test');
const assert=require('node:assert/strict');
const Preview=require('../frontend/bridge/attachment-preview.js');
global.IconSystem=require('../gateway/internal/server/static/icons.js');
const result=(modality,format,extra={})=>({manifest:{manifest_id:'file_one',original_name:'record.'+format,modality,source_format:format,...extra},file_url:'/sessions/one/attachments/file_one/file'});

test('uploaded images render the actual raster with a zoom target and dimensions',()=>{
 const html=Preview.body(result('image','png',{width:1280,height:720}),String);
 assert.match(html,/<img[^>]+src="\/sessions\/one\/attachments\/file_one\/file"[^>]+data-preview-zoom/);
 assert.match(html,/图片 · PNG/);assert.match(html,/1280 × 720/);assert.doesNotMatch(html,/<iframe/);
});

test('video and PDF previews embed local players with separate original-file actions',()=>{
 const video=Preview.body(result('video','mp4',{duration_s:20,width:640,height:480}),String);
 assert.match(video,/<video[^>]+controls/);assert.match(video,/preload="metadata"/);assert.doesNotMatch(video,/autoplay/);
 assert.match(video,/\?download=1/);assert.match(video,/在新标签页打开/);
 const pdf=Preview.body(result('document','pdf'),String);assert.match(pdf,/<iframe[^>]+#view=FitH/);
});

test('EEG displays its true waveform identity and recording scope rather than a binary iframe',()=>{
 const eeg=result('eeg','npz',{n_channels:2,sample_rate_hz:128,duration_s:63,channel_names:['P3','P4'],unit:{value:'dataset_native'},trial_count:40,trial_index:1});
 eeg.preview={artifact_id:'art_wave'};
 const html=Preview.body(eeg,String);
 assert.match(html,/\/artifacts\/art_wave\/file/);assert.match(html,/128 Hz/);assert.match(html,/第 2 个/);assert.doesNotMatch(html,/<iframe/);
 assert.match(html,/40 个试次/);assert.match(html,/当前试次 63 秒/);assert.match(html,/下方波形对应此试次/);
 assert.match(html,/aria-label="返回文件列表"/);assert.doesNotMatch(html,/>返回全部文件</);
});

test('opening a file inside a folder returns to that folder before returning to the conversation files',async()=>{
 let click;
 const container={innerHTML:'',hidden:true,replaceChildren(){this.innerHTML=''},querySelector(){return null},addEventListener(name,listener){click=listener}};
 const folder={workspace:{name:'采集数据'},path:'.',entries:[{path:'说明.md',name:'说明.md',directory:false}]};
 const viewer=Preview.mount(container,{load:async id=>id.kind==='directory'?folder:result('document','md'),markdown:String});
 await viewer.open({workspace:'ws_one',path:'.',kind:'directory'});
 click({target:{closest:()=>({dataset:{previewPath:'说明.md',previewKind:'file'}})}});await new Promise(setImmediate);
 assert.match(container.innerHTML,/record.md/);assert.match(container.innerHTML,/返回上一级/);
 assert.equal(viewer.back(),true);await new Promise(setImmediate);assert.match(container.innerHTML,/采集数据/);assert.equal(viewer.back(),false);
});

test('text previews escape source HTML and mark a genuinely truncated excerpt',()=>{
 const text={...result('eeg','csv',{n_channels:1,n_samples:10}),content:'<script>alert(1)</script>',content_truncated:true};
 const html=Preview.body(text,String);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);assert.match(html,/文件开头/);
 const markdown={...result('document','md'),content:'# Methods'};
 assert.match(Preview.body(markdown,text=>'<p>'+text+'</p>'),/<p># Methods<\/p>/);
});

test('a later selected file owns the preview even when an earlier response finishes last',async()=>{
 const container={innerHTML:'',hidden:true,replaceChildren(){this.innerHTML=''},querySelector(){return null},addEventListener(){}};
 let first;const viewer=Preview.mount(container,{load:id=>id==='old'?new Promise(resolve=>first=resolve):Promise.resolve(result('document','pdf')),markdown:String});
 const pending=viewer.open('old');await viewer.open('new');const current=container.innerHTML;
 first(result('video','mp4'));await pending;assert.equal(container.innerHTML,current);
 const stale=viewer.open('old');viewer.reset();first(result('video','mp4'));await stale;assert.equal(container.hidden,true);assert.equal(container.innerHTML,'');
});

test('closing or replacing a preview releases its video playback',()=>{
 const events=[];const video={pause:()=>events.push('pause'),removeAttribute:name=>events.push(name),load:()=>events.push('load')};
 const container={hidden:false,querySelector:()=>video,replaceChildren(){events.push('clear')},addEventListener(){}};
 Preview.mount(container,{load:async()=>{},markdown:String}).reset();assert.deepEqual(events,['pause','src','load','clear']);
});
