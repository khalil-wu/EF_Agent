/* Public transcript rules, shared by live rendering and history replay. */
const TranscriptProjection=(()=>{
  const internalTools=new Set(['read_skill','request_user_input']);
  function messageKind(message){
    if(message.role==='user')return /^<(reflection|compaction_summary|file_context|latest_context)>/.test(message.content)?'internal':'user';
    if(message.role==='assistant')return message.phase?({commentary:'commentary',final_answer:'answer',interrupted:'partial'})[message.phase]:message.tool_calls?.length?'commentary':'answer';
    return message.role==='tool'?'tool':'internal';
  }
  function toolVisible(name){return !internalTools.has(name)}
  function publicArtifacts(items,text){
    return items.filter(item=>['plot','report','table','prediction','aggregate'].includes(item.kind)||text.includes(item.artifact_id));
  }
  function imageReferences(text){return [...text.matchAll(/!\[[^\]]*\]\(artifact:\/\/(art_[0-9a-f]+)\)/g)].map(match=>match[1])}
  function userPresentation(message){return message.presentation||{text:message.content,attachments:[],questions:[]}}
  function activityLabel(steps,running){
    if(running){const current=[...steps].reverse().find(step=>step.classList.contains('run'));return current?current.querySelector('.name').textContent:'正在分析'}
    const failed=[...steps].find(step=>step.classList.contains('err'));
    if(failed)return failed.querySelector('.name').textContent+'未完成';
    const names=[...new Set([...steps].map(step=>step.querySelector('.name').textContent))];
    return names.length>2?names[0]+'、'+names.at(-1)+'等操作':names.join('、')||'分析过程';
  }
  function toolSummary(name,result){
    if(name==='recognize_emotion')return result.prediction_executed?`已识别 ${result.summary.prediction_count} 个时间窗`:'当前输入尚不满足识别条件';
    if(name==='route_model')return result.selected_model?'模型条件已确认':'分类模型暂不可运行';
    if(name==='plot')return '图表已生成';
    if(name==='preprocess'||name==='clean_artifacts')return '处理信号已保存';
    if(name==='segment_signal')return '分析时间窗已准备';
    if(name==='extract_features')return '特征已计算';
    if(name==='run_python')return '分析脚本已执行';
    if(name==='search_knowledge'||name==='knowledge_graph')return '已读取相关依据';
    if(name==='update_record_metadata')return '确认信息已保存';
    if(name==='export')return '文件已导出';
    return '已完成';
  }
  return {messageKind,toolVisible,publicArtifacts,imageReferences,userPresentation,activityLabel,toolSummary};
})();
if(typeof module!=='undefined')module.exports=TranscriptProjection;
