/* User-facing method and output details for a selected execution step. */
const ExecutionDetails=(()=>{
  const escape=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const present=value=>value!==undefined&&value!==null&&value!=='';
  const joined=values=>values.filter(present).join(' · ');
  const features={psd:'功率谱',bandpower:'频带功率',de:'差分熵',hjorth:'Hjorth 参数',band_stats:'频带统计',spectral_entropy:'谱熵',asymmetry:'额叶不对称',connectivity:'通道连接性',rms:'均方根幅值',line_length:'线长',zero_crossing_rate:'过零率'};
  const methods={ica_iclabel:'ICA + ICLabel',asr:'伪迹子空间重建（ASR）',bad_channels:'坏道检测（PREP）'};
  const kinds={plot:'图表',table:'数据表',report:'分析报告',features:'特征数据',windows:'时间窗',normalized_signal:'处理后的信号',prediction:'模型预测',emotion_assessment:'情绪评估记录',aggregate:'汇总结果',python:'复现脚本'};
  const seconds=value=>present(value)?value+' s':'';
  const hz=value=>present(value)?value+' Hz':'';
  const range=value=>Array.isArray(value)&&value.length===2?value.join('–')+' s':'';
  function values(rows){return '<dl class="execution-values">'+rows.filter(([,value])=>present(value)).map(([label,value])=>'<div><dt>'+escape(label)+'</dt><dd>'+escape(value)+'</dd></div>').join('')+'</dl>'}
  function section(title,body){return body?'<section class="execution-section"><h4>'+escape(title)+'</h4>'+body+'</section>':''}
  function rowsSection(title,rows){const populated=rows.filter(([,value])=>present(value));return populated.length?section(title,values(populated)):''}
  function stepText(step){
    switch(step.op){
      case 'pick_eeg':return '保留 EEG 通道';
      case 'pick_channels':return joined(['选择通道',(step.channels||[]).join('、')]);
      case 'rereference':return joined(['重设参考',step.method==='car'?'平均参考':step.channel||step.method]);
      case 'notch':return joined(['工频陷波',hz(step.freq_hz??step.freq),present(step.q)?'Q = '+step.q:'']);
      case 'bandpass':{const low=step.low_hz??step.low,high=step.high_hz??step.high;return joined(['带通滤波',present(low)&&present(high)?low+'–'+high+' Hz':'',step.method?.toUpperCase(),present(step.order)?step.order+' 阶':'',step.phase==='zero'?'零相位':''])}
      case 'resample':return joined(['重采样',hz(step.output_sample_rate_hz??step.target_hz)]);
      case 'remove_baseline':return joined(['去基线',seconds(step.actual_seconds??step.seconds),step.drop===true?'移除基线段':step.drop===false?'保留基线段':'']);
      case 'normalize':return joined(['标准化',step.method]);
      default:return '';
    }
  }
  function render(entry,{manifests=[],artifacts={}}={}){
    const {name}=entry,args=entry.args||{},result=entry.result||{},summary=result.summary||{},outputRecords=[];
    for(const record of [result.artifact,...(result.artifacts||[])]){
      const item=typeof record==='string'?artifacts[record]:record;
      if(item?.artifact_id&&!outputRecords.some(other=>other.artifact_id===item.artifact_id))outputRecords.push(item);
    }
    const source=artifacts[args.source_id||args.windows_id||args.artifact_id];
    const sourceIDs=source?.source_manifest_ids||outputRecords[0]?.source_manifest_ids||[];
    const manifest=name==='workspace_quality'||name==='run_python'?undefined:result.manifest||manifests.find(item=>item.manifest_id===(args.manifest_id||args.source_id))||(sourceIDs.length===1?manifests.find(item=>item.manifest_id===sourceIDs[0]):undefined);
    const input=[],method=[];
    const add=(rows,label,value)=>{if(present(value))rows.push([label,value])};
    add(input,'文件',args.path||manifest?.original_name||manifest?.source_path);
    if(name!=='workspace_quality'&&manifest){
      if(present(manifest.trial_index))add(input,'试次','第 '+(manifest.trial_index+1)+(manifest.trial_count?' / '+manifest.trial_count:'')+' 个试次'+(manifest.trial_selection==='default_first'?'（仅默认首试次）':''));
      else if(manifest.trial_count>1)add(input,'试次范围','记录含 '+manifest.trial_count+' 个试次；本步骤未给出所选试次');
      add(input,'输入采样率',hz(manifest.sample_rate_hz));
      add(input,'输入单位',({dataset_native:'原始数值（物理单位未指定）',unknown:'物理单位未指定',uV:'µV'})[manifest.unit?.value]||manifest.unit?.value);
    }
    const actualRange=summary.time_range_s||summary.covers_s||summary.quality?.sample_time_range_s||summary.coverage?.span_s||result.artifact?.time_range_s||(name==='inspect_record'?manifest?.quality?.sample_time_range_s:undefined);
    add(input,'分析时间范围',range(actualRange)||(present(args.start_s)&&present(args.end_s)?args.start_s+'–'+args.end_s+' s':present(args.start_s)?'从 '+seconds(args.start_s):present(args.end_s)?'至 '+seconds(args.end_s):''));
    add(input,'通道范围',args.channels?.join('、'));
    switch(name){
      case 'list_files':add(input,'目录',args.root);add(input,'文件筛选',args.pattern);add(input,'文件类型',({eeg:'EEG',video:'视频',any:'全部',all:'全部'})[args.kind]);break;
      case 'read_file':if(present(result.start_line))add(input,'读取范围','第 '+result.start_line+'–'+result.end_line+' 行');else if(present(args.offset))add(input,'起始行',args.offset);break;
      case 'read_document':add(input,'起始页',args.page);add(input,'页数上限',args.limit);break;
      case 'workspace_files':add(input,'文件范围',args.paths?.join('、'));add(input,'目标位置',args.destination);break;
      case 'workspace_quality':add(input,'文件筛选',args.pattern);add(input,'已排除',summary.excluded?.join('、'));add(method,'已检查试次',summary.measured);add(method,'未完成检查的试次',summary.not_measured);add(method,'指定采样率',hz(args.sample_rate_hz));break;
      case 'inspect_record':add(method,'质量检查范围',({full:'所选记录 / 试次的完整范围',initial:'开头初筛'})[args.quality_scope||manifest?.quality?.scope]);break;
      case 'preprocess':add(method,'输出采样率',hz(summary.sample_rate_hz));break;
      case 'clean_artifacts':{
        const actual=summary.method||args.method;
        add(method,'伪迹处理',methods[actual]||actual);
        if(actual==='ica_iclabel'){
          add(method,'剔除概率阈值',summary.threshold??args.threshold);add(method,'ICA 成分数',summary.n_components??args.n_components);
          add(method,'剔除类别',(summary.exclude_labels||args.exclude)?.join('、'));add(method,'拟合时长',seconds(summary.fit?.fit_seconds));
        }else if(actual==='asr')add(method,'标准差阈值',summary.cutoff??args.cutoff);
        else if(actual==='bad_channels'){
          add(method,'坏道处理',({interpolate:'插值',drop:'移除',mark:'仅标记',none:'无需修改'})[summary.action||args.action]);
          const ransac=summary.ransac??args.ransac;add(method,'RANSAC',present(ransac)?ransac?'启用':'关闭':'');
        }
        break;
      }
      case 'segment_signal':case 'analyze_signal':case 'extract_features':{
        const window=summary.window_support||result.artifact?.analysis?.window_support||outputRecords.find(record=>record.kind==='windows')?.analysis?.window_support||source?.analysis?.window_support||source?.summary||{},feature=summary.features&&typeof summary.features==='object'&&!Array.isArray(summary.features)?summary.features:summary;
        add(method,'窗长',seconds(summary.window_s??window.window_s??args.window_s));add(method,'步长',seconds(summary.stride_s??window.stride_s??args.stride_s));
        add(method,'末窗处理',({drop:'丢弃不完整末窗',keep:'保留不完整末窗',pad:'补齐不完整末窗'})[window.partial||args.partial]);
        add(method,'特征',(feature.features||args.features)?.map(value=>features[value]||value).join('、'));
        if(feature.bands&&typeof feature.bands==='object')add(method,'实际频带',Object.entries(feature.bands).map(([band,bounds])=>band+' '+bounds.join('–')+' Hz').join('；'));
        else add(method,'频带方案',args.bands);
        add(method,'差分熵方法',feature.de_method||args.de_method);
        if(feature.de_bands)add(method,'差分熵频带',Object.entries(feature.de_bands).map(([band,bounds])=>band+' '+bounds.join('–')+' Hz').join('；'));
        add(method,'幅值单位',feature.amplitude_unit);add(method,'功率单位',feature.power_unit);
        if(name==='analyze_signal'&&summary.prediction_executed===false)add(method,'情绪分类','本步骤未运行分类模型');
        break;
      }
      case 'run_model':case 'recognize_emotion':case 'route_model':case 'check_model_compat':
        add(method,'模型',summary.model_id||result.selected_model||args.model_id);add(method,'模型版本',summary.model_version);
        add(method,'评估记录',({test_selected:'历史探索权重：按测试集选模',independent_test:'独立测试集评估',not_recorded:'独立评估未登记'})[summary.model_evaluation?.status]);
        add(method,'评估划分',summary.model_evaluation?.split_protocol);
        add(method,'分数类型',({logits:'原始模型分数（logits）',probabilities:'类别概率'})[summary.score_kind]);
        add(input,'请求试次上限',args.max_trials);add(input,'视频',manifests.find(item=>item.manifest_id===args.video_manifest_id)?.original_name);
        add(method,'视频用途',({face:'面部录像',stimulus:'刺激材料',unknown:'尚未确认'})[args.video_role]);add(method,'同步偏移',seconds(args.sync_offset_s));break;
      case 'plot':add(method,'图表类型',({raw:'EEG 波形',psd:'功率谱',bandpower_timeline:'频带功率趋势',timeline:'时间趋势'})[args.kind]||args.kind);add(method,'频带',args.band);break;
      case 'export':add(method,'导出格式',args.kind?.toUpperCase());break;
    }
    let html=rowsSection('输入与范围',input);
    const steps=name==='preprocess'?(summary.processing_settings||args.steps||[]).map(stepText).filter(Boolean):[];
    const methodBody=(steps.length?'<ol class="execution-steps">'+steps.map(step=>'<li>'+escape(step)+'</li>').join('')+'</ol>':'')+(method.length?values(method):'');
    html+=section('分析方法',methodBody);
    const outputButtons=outputRecords.map(record=>{const kind=record.media_type==='text/x-python'||record.summary?.format==='py'?'复现脚本':kinds[record.kind]||record.kind||'分析产物';return '<button type="button" class="execution-output" data-execution-artifact="'+escape(record.artifact_id)+'"><span>'+escape(record.summary?.title||kind)+'</span><small>'+escape(kind)+'</small></button>'});
    const files=new Map();
    for(const file of [result.workspace_file,...(result.workspace_files||[])])if(file?.workspace_id&&file.path)files.set(file.workspace_id+':'+file.path,file);
    for(const file of files.values())outputButtons.push('<button type="button" class="execution-output" data-execution-workspace="'+escape(file.workspace_id)+'" data-execution-path="'+escape(file.path)+'" data-execution-kind="'+escape(file.kind||'file')+'"><span>'+escape(file.name||file.path.split(/[\\/]/).at(-1))+'</span><small>'+escape(file.path)+'</small></button>');
    html+=section('实际产物',outputButtons.length?'<div class="execution-outputs">'+outputButtons.join('')+'</div>':'');
    const code=name==='run_python'?args.code:name==='write_file'&&args.path?.endsWith('.py')?args.content:name==='read_file'&&args.path?.endsWith('.py')?result.content:'';
    if(name==='run_python'&&result.code_path)html+=rowsSection('执行脚本',[['脚本路径',result.code_path]]);
    if(code)html+='<details class="execution-code"><summary>查看执行脚本</summary><pre><code>'+escape(code)+'</code></pre></details>';
    return html;
  }
  return {render};
})();
