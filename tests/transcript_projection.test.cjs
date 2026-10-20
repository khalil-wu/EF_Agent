const {test}=require('node:test');
const assert=require('node:assert/strict');
const projection=require('../gateway/internal/server/static/projection.js');

test('public commentary survives separately from the final answer',()=>{
 assert.equal(projection.messageKind({role:'assistant',content:'先检查伪迹。',tool_calls:[{id:'call'}]}),'commentary');
 assert.equal(projection.messageKind({role:'assistant',content:'主峰为 10 Hz。'}),'answer');
 assert.equal(projection.messageKind({role:'user',content:'<reflection>continue</reflection>'}),'internal');
});

test('explicit message phases drive projection and interrupted text is not a final answer',()=>{
 assert.equal(projection.messageKind({role:'assistant',phase:'commentary',content:'检查资料。'}),'commentary');
 assert.equal(projection.messageKind({role:'assistant',phase:'final_answer',tool_calls:[{id:'legacy'}],content:'结果。'}),'answer');
 assert.equal(projection.messageKind({role:'assistant',phase:'interrupted',content:'未完成的文字'}),'partial');
});
test('skills and user prompts are not projected as executed analysis steps',()=>{
 assert.equal(projection.toolVisible('read_skill'),false);
 assert.equal(projection.toolVisible('request_user_input'),false);
 assert.equal(projection.toolVisible('preprocess'),true);
});
test('intermediate data stays out of the result gallery unless explicitly referenced',()=>{
 const items=[{artifact_id:'art_1',kind:'normalized_signal'},{artifact_id:'art_2',kind:'windows'},{artifact_id:'art_3',kind:'features'},{artifact_id:'art_4',kind:'plot'},{artifact_id:'art_5',kind:'table'}];
 assert.deepEqual(projection.publicArtifacts(items,'主峰为 10 Hz。').map(x=>x.artifact_id),['art_4','art_5']);
 assert.deepEqual(projection.publicArtifacts(items,'下载处理信号 artifact://art_1').map(x=>x.artifact_id),['art_1','art_4','art_5']);
});
test('embedded figure ids are available for gallery deduplication',()=>{
 assert.deepEqual(projection.imageReferences('结果 ![功率谱](artifact://art_ab12) [数据](artifact://art_cd34)'),['art_ab12']);
});
test('the human reply is displayed while the model keeps the full question context',()=>{
 const message={role:'user',content:'采样率是多少？\n128 Hz',presentation:{text:'128 Hz',questions:['采样率是多少？'],attachments:[]}};
 assert.equal(projection.userPresentation(message).text,'128 Hz');
 assert.equal(message.content,'采样率是多少？\n128 Hz');
});
test('script progress does not expose stdout counters',()=>{
 assert.equal(projection.toolSummary('run_python',{stdout:'raw',artifacts:[]}), '分析脚本已执行');
});
test('work summaries identify the pending action and preserve a failed action after completion',()=>{
 const step=(name,state)=>({classList:{contains:value=>value===state},querySelector:()=>({textContent:name})});
 const steps=[step('检查数据','ok'),step('生成图表','run')];
 assert.equal(projection.activityLabel(steps,true),'生成图表');
 assert.equal(projection.activityLabel([steps[0],step('生成图表','err')],false),'生成图表未完成');
 assert.equal(projection.activityLabel([steps[0],step('检查数据','ok'),step('生成图表','ok')],false),'检查数据、生成图表');
});
