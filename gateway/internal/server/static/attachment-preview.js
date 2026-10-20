/* Uploaded-file viewer; the conversation and scientific tools retain their own state. */
const AttachmentPreview=(()=>{
  const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon=name=>`<span class="ms" aria-hidden="true">${IconSystem.svg(name)}</span>`;
  const back=(label='返回文件列表')=>`<button type="button" class="icon-btn attachment-back" data-attachment-back aria-label="${label}" title="${label}">${icon('chevron_left')}</button>`;
  function toolbar(name,type,url,label){
    return `<header class="attachment-toolbar">${back(label)}<div class="attachment-title"><h3 title="${esc(name)}">${esc(name)}</h3><span>${esc(type)}</span></div>${url?`<a class="icon-btn" href="${esc(url)}${url.includes('?')?'&':'?'}download=1" download title="下载原文件" aria-label="下载原文件">${icon('download')}</a><a class="icon-btn" href="${esc(url)}" target="_blank" rel="noopener" title="在新标签页打开" aria-label="在新标签页打开">${icon('open_in_new')}</a>`:''}</header>`;
  }
  function body(result,markdown){
    if(result.entries){
      const name=result.path==='.'?result.workspace.name:result.path.split('/').at(-1);
      return toolbar(name,'文件夹',null,result.back_label)+`<div class="preview-breadcrumb" title="${esc(result.path)}">${esc(result.path)}</div><div class="folder-preview-list">${result.entries.map(item=>`<button type="button" data-preview-path="${esc(item.path)}" data-preview-kind="${item.directory?'directory':'file'}" title="${esc(item.path)}">${icon(item.directory?'folder_open':'description')}<span>${esc(item.name)}</span>${item.excluded?'<small>已排除分析</small>':''}${icon('chevron_right')}</button>`).join('')||'<p class="attachment-preview-info">文件夹为空</p>'}</div>`;
    }
    const m=result.manifest,url=result.file_url,format=(m.source_format||'').toLowerCase();
    const type=m.modality==='eeg'?'EEG 记录':m.modality==='video'?'视频':m.modality==='image'?'图片':format==='pdf'?'PDF 文档':'文档';
    let html=toolbar(m.original_name||m.manifest_id,type+(format?' · '+format.toUpperCase():''),url,result.back_label);
    if(result.display_path?.includes('/'))html+=`<div class="preview-breadcrumb" title="${esc(result.display_path)}">${esc(result.display_path)}</div>`;
    if(m.modality==='image'){
      html+=`<div class="attachment-wave"><img src="${esc(url)}" alt="${esc(m.original_name)}" data-preview-zoom></div><p class="attachment-preview-info">${m.width} × ${m.height}</p>`;
    }else if(m.modality==='video'){
      html+=`<video class="attachment-video" src="${esc(url)}" controls playsinline preload="metadata" aria-label="${esc(m.original_name)}视频预览"></video><p class="attachment-preview-info">${m.duration_s!=null?esc(m.duration_s)+' 秒':''}${m.width&&m.height?' · '+m.width+' × '+m.height:''}</p>`;
    }else if(m.modality==='document'&&format==='pdf'){
      html+=`<iframe class="attachment-pdf" src="${esc(url)}#view=FitH" title="${esc(m.original_name)} PDF 预览"></iframe>`;
    }else if(m.modality==='eeg'){
      html+=`<div class="attachment-signal-info"><span>${m.n_channels} 通道</span><span>${m.sample_rate_hz==null?'采样率待补充':esc(m.sample_rate_hz)+' Hz'}</span>${m.trial_count>1?`<span>${m.trial_count} 个试次</span>`:''}<span>${m.signal_kind==='epochs'?'累计采样 ':m.trial_count>1?'当前试次 ':''}${m.duration_s==null?m.n_samples+' 点':esc(m.duration_s)+' 秒'}</span></div>`;
      if(m.trial_count>1)html+=`<p class="attachment-preview-info">当前第 ${m.trial_index+1} 个试次${m.trial_selection==='default_first'?'（默认）':''} · 下方波形对应此试次。</p>`;
      if(result.preview)html+=`<div class="attachment-wave"><img src="/artifacts/${esc(result.preview.artifact_id)}/file" alt="${esc(m.original_name)} EEG 波形预览" data-preview-zoom></div>`;
      if(result.preview_note)html+=`<p class="attachment-preview-info">${esc(result.preview_note)}</p>`;
      html+=`<details class="attachment-details"><summary>通道与记录信息</summary><dl><dt>通道</dt><dd>${esc((m.channel_names||[]).join(', '))}</dd><dt>格式</dt><dd>${esc(format.toUpperCase())}</dd><dt>单位</dt><dd>${esc(m.unit?.value||'原始数值')}</dd></dl></details>`;
    }
    if(result.content!==undefined){
      html+=format==='md'||format==='markdown'?`<div class="attachment-document md">${markdown(result.content)}</div>`:`<pre class="attachment-text">${esc(result.content)}</pre>`;
      if(result.content_truncated)html+='<p class="attachment-preview-info">这里显示文件开头的内容；下载或打开原文件可查看全文。</p>';
    }
    return html;
  }
  function mount(container,{load,markdown}){
    let revision=0,target=null,history=[];
    function stopMedia(){const video=container.querySelector('video');if(video){video.pause();video.removeAttribute('src');video.load()}}
    async function show(id){
        target=id;
        const request=++revision;stopMedia();container.hidden=false;
        const label=history.length?'返回上一级':'返回文件列表';
        container.innerHTML=toolbar('文件预览','',null,label)+'<p class="attachment-preview-info" role="status">正在读取文件预览…</p>';
        try{
          const result=await load(id);
          if(request!==revision)return;
          container.innerHTML=body({...result,back_label:label},markdown);
        }catch(error){
          if(request!==revision)return;
          container.innerHTML=toolbar('文件预览','',null,label)+`<p class="attachment-preview-error" role="alert">${esc(error.message)}</p>`;
        }
    }
    container.addEventListener('click',event=>{const item=event.target.closest('[data-preview-path]');if(item){history.push(target);show({workspace:target.workspace,path:item.dataset.previewPath,kind:item.dataset.previewKind})}});
    return {
      reset(){revision++;history=[];target=null;stopMedia();container.hidden=true;container.replaceChildren()},
      open(id){history=[];return show(id)},
      back(){if(!history.length)return false;show(history.pop());return true},
    };
  }
  return {body,mount};
})();
if(typeof module!=='undefined')module.exports=AttachmentPreview;
