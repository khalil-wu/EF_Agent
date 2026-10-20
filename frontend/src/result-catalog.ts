const plots:Record<string,[string,string]>={
  raw:['EEG 波形','各通道振幅随时间的变化。'],
  psd:['功率谱密度（PSD）','信号功率随频率的分布，用于查看节律主峰。'],
  bandpower_timeline:['频带功率时间线','各分析窗口的频带功率变化。'],
  timeline:['时间趋势','分析指标随时间的变化。'],
  overview:['EEG 分析总览','波形、功率谱和频带趋势的综合对照。'],
  clean_ica_iclabel:['ICA 成分头皮分布','各独立成分的头皮空间权重，以及 ICLabel 分类和剔除状态。'],
  clean_asr:['ASR 清洗前后对照','对照 ASR 处理前后的通道波形。']
};
const labels:Record<string,string>={report:'分析报告',plot:'分析图表',features:'特征数组',windows:'分窗数据',normalized_signal:'处理后的信号',table:'数据表',prediction:'模型预测',emotion_assessment:'情绪特征审阅记录',aggregate:'汇总数据',compat_report:'模型输入检查'};
export function resultGroupTitle(row:any,lookup:(id:string)=>any,sourceName:(id:string)=>string){
  const items=resultItems(row.artifacts,lookup),tools=row.entries.filter((entry:any)=>entry.type==='tool');
  const methods=new Set(items.map(item=>item.summary?.method).filter(Boolean));
  for(const tool of tools)if(tool.name==='clean_artifacts')methods.add(tool.args.method);
  const ica=methods.has('ica_iclabel')||items.some(item=>item.summary?.kind==='clean_ica_iclabel');
  const asr=methods.has('asr')||items.some(item=>item.summary?.kind==='clean_asr');
  let title=ica&&asr?'ICA / ASR 方法对照':ica?'ICA 成分分析':asr?(items.filter(item=>item.kind==='normalized_signal').length>1?'批量 ASR 清洗':'ASR 清洗'):'';
  if(!title){
    if(tools.some((tool:any)=>['run_model','recognize_emotion'].includes(tool.name)))title='情绪识别';
    else if(tools.some((tool:any)=>tool.name==='analyze_signal'))title='信号分析';
    else if(tools.some((tool:any)=>tool.name==='workspace_quality'))title='批量质量检查';
    else if(items.some(item=>item.kind==='features'))title='特征分析';
    else if(items.some(item=>item.kind==='plot'))title=items.filter(item=>item.kind==='plot').length===1?resultTitle(items.find(item=>item.kind==='plot')):'图表分析';
    else if(items.some(item=>item.kind==='report'))title='分析报告';
    else if(items.some(item=>item.kind==='table'))title='数据导出';
    else title='文件交付';
  }
  const sources=[...new Set(items.flatMap(item=>item.source_manifest_ids||[]).map(sourceName).filter(Boolean))];
  return title+(sources.length?' · '+sources.slice(0,2).join('、'):'');
}
export function workspaceDeliveries(row:any){
  const deliveries=new Map<string,any>();
  for(const tool of row.entries.filter((entry:any)=>entry.type==='tool')){
    const files=[...(tool.result?.workspace_files||[]),...(tool.result?.workspace_file?[tool.result.workspace_file]:[])];
    if(!files.length)continue;
    const unique=[...new Map(files.map((file:any)=>[file.workspace_id+':'+file.path,file])).values()] as any[];
    const count=tool.result.workspace_files_count??unique.length;
    const key=unique.map(file=>file.workspace_id+':'+file.path).sort().join('|')+':'+count;
    deliveries.set(key,{id:tool.id,files:unique,count});
  }
  const batches=[...deliveries.values()];
  if(batches.some(batch=>batch.count>batch.files.length))return batches;
  const workspaces=new Map<string,{id:string;files:Map<string,any>}>();
  for(const batch of batches)for(const file of batch.files){
    let workspace=workspaces.get(file.workspace_id);
    if(!workspace){workspace={id:'workspace-'+file.workspace_id,files:new Map()};workspaces.set(file.workspace_id,workspace)}
    workspace.files.set(file.path,file);
  }
  return [...workspaces.values()].map(workspace=>({id:workspace.id,files:[...workspace.files.values()],count:workspace.files.size}));
}
export function workspaceSummary(deliveries:any[]):string{
  if(deliveries.length===1)return `${deliveries[0].count} 个工作区文件`;
  if(deliveries.every(delivery=>delivery.count===delivery.files.length))return `${deliveries.reduce((sum,delivery)=>sum+delivery.count,0)} 个工作区文件`;
  return '已保存工作区文件';
}
export function resultTitle(item:any):string{
  const s=item.summary||{};
  if(s.title)return s.title;
  if(item.kind==='plot')return plots[s.kind]?.[0]||'分析图表';
  if(item.kind==='table'&&s.format)return s.features?.length||s.columns?.includes('feature')&&s.columns?.includes('window')?'逐窗特征':s.label_definitions?'预测结果':s.columns?.includes('channel')?'通道数据':'数据表';
  if(item.kind==='prediction')return s.method==='model_inference'?'模型预测':'预测产物';
  if(item.kind==='emotion_assessment'&&s.assessment_status==='hypothesis')return '候选情绪假设';
  return labels[item.kind]||'分析文件';
}
export function resultDescription(item:any):string{
  const s=item.summary||{};
  if(item.kind==='normalized_signal'&&s.method==='asr')return `ASR 清洗 · 截止参数 ${s.cutoff}${s.n_channels?' · '+s.n_channels+' 通道':''}`;
  return s.description||plots[s.kind]?.[1]||s.caption||'';
}
export function resultFormat(item:any):string{
  if(item.summary?.format)return item.summary.format.toUpperCase();
  const extension=item.uri?.match(/\.(npy|npz|csv|json|md|pdf|png|svg|jpg|jpeg)$/i)?.[1];
  if(extension&&item.kind!=='plot')return extension.toUpperCase();
  if(item.kind==='plot')return (item.summary?.available_formats||['png']).map((value:string)=>value.toUpperCase()).join(' / ');
  return ({features:'NPZ',windows:'NPZ',normalized_signal:'NPZ',report:'报告',table:'表格',prediction:'预测结果',emotion_assessment:'评估记录',aggregate:'汇总'} as Record<string,string>)[item.kind]||'数据';
}
export const isProcessResult=(item:any)=>['features','windows','compat_report'].includes(item.kind);
function identity(item:any,lookup:(id:string)=>any){
  if(item.kind!=='table'||!item.summary?.format)return item.artifact_id;
  let source=item;
  while(source.kind==='table'&&(source.provenance?.created_by||source.created_by||'').startsWith('export@')&&source.summary?.source){
    const parent=lookup(source.summary.source);
    if(!parent)return source.summary.source+':'+item.summary.format;
    source=parent;
  }
  return source.artifact_id+':'+item.summary.format;
}
export function resultItems(ids:string[],lookup:(id:string)=>any){
  const items=new Map<string,any>();
  for(const id of ids){const item=lookup(id);if(item&&!items.has(identity(item,lookup)))items.set(identity(item,lookup),item)}
  return [...items.values()];
}
export function resultSummary(items:any[]):string{
  const counts=[['张图',items.filter(item=>item.kind==='plot').length],['份表',items.filter(item=>item.kind==='table').length],['份报告',items.filter(item=>item.kind==='report').length],['份 EEG 数据',items.filter(item=>item.kind==='normalized_signal').length],['项结果',items.filter(item=>!['plot','table','report','normalized_signal'].includes(item.kind)&&!isProcessResult(item)).length],['项过程数据',items.filter(isProcessResult).length]] as const;
  return counts.filter(([,count])=>count).map(([label,count])=>count+' '+label).join(' · ')||items.length+' 项过程数据';
}
