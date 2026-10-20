const {test}=require('node:test');
const assert=require('node:assert/strict');
const citations=require('../gateway/internal/server/static/citations.js');

test('citation ids in code are not interpreted as evidence',()=>{
 assert.deepEqual(citations.references('依据 [k_abcdef] 与 k_123456。`k_deadbeef`\n```json\n"k_001122"\n```'),['k_abcdef','k_123456']);
});

test('an uploaded source opens its registered original file at the cited page',()=>{
 const source={title:'研究.pdf',page:2,manifest_id:'doc_123456',source_kind:'user_upload',text:'原文'};
 assert.equal(citations.typeLabel(source),'上传文档');
 assert.equal(citations.original(source,'ses_abcdef').url,'/sessions/ses_abcdef/attachments/doc_123456/file#page=2');
});

test('project notes are shown as notes and only registered snapshots open as original text',()=>{
 const digest='a'.repeat(64),source={title:'方法笔记',source_kind:'project_note',text:'## 方法\n摘录正文。',original_url:'/knowledge/documents/'+digest+'/file'};
 assert.equal(citations.typeLabel(source),'知识笔记');
 assert.equal(citations.excerpt(source),'摘录正文。');
 assert.equal(citations.original(source,'ses_abcdef').label,'打开笔记快照');
 assert.equal(citations.original({...source,original_url:'javascript:alert(1)'},'ses_abcdef'),null);
});

test('copying an answer includes readable citations and matching bibliography without altering code',()=>{
 const source={title:'研究.pdf',page:2,manifest_id:'doc_123456',source_kind:'user_upload',text:'实测原文'};
 const copied=citations.copyText('如文档所述 [页码](k_abcdef)。`k_abcdef`',{k_abcdef:source},'ses_abcdef','http://127.0.0.1:8080');
 assert.match(copied,/页码\[1\]/);
 assert.match(copied,/`k_abcdef`/);
 assert.match(copied,/\[1\] 研究.pdf · 第 2 页（上传文档）/);
 assert.match(copied,/file#page=2/);
 assert.equal(citations.copyText('未定位 [k_001122]',{},'ses_abcdef','http://127.0.0.1:8080'),'未定位 [引用未定位]');
});
