/* Citation identity belongs to retrieved evidence, never to a model-invented URL. */
const SourceCitations=(()=>{
  function references(text){
    const prose=text.replace(/```[\s\S]*?```|`[^`]*`/g,'');
    return [...new Set(prose.match(/\bk_[0-9a-f]{6,}\b/g)||[])];
  }
  function kind(source){return source.source_kind||(source.doc_type==='document'?'user_upload':source.doc_type==='paper'?'paper_excerpt':'project_note')}
  function typeLabel(source){return {user_upload:'上传文档',paper_excerpt:'论文解析文本',project_note:'知识笔记',official_documentation:'官方文档'}[kind(source)]||'参考资料'}
  function location(source){return source.page?'第 '+source.page+' 页':source.section&&source.section!=='（引言）'?source.section:'正文'}
  function title(source){return source.title||source.doc_id||'参考资料'}
  function excerpt(source){return source.text.replace(/^#{1,6}\s+.*\n?/,'').trim()}
  function original(source,session){
    if(source.manifest_id)return {label:'打开原文件',url:`/sessions/${encodeURIComponent(session)}/attachments/${encodeURIComponent(source.manifest_id)}/file#page=${source.page||1}`};
    if(/^\/knowledge\/documents\/[0-9a-f]{64}\/file\?format=(pdf|html)$/.test(source.source_original_url||''))return {label:source.source_original_url.endsWith('pdf')?'打开原始论文 PDF':'下载来源网页快照',url:source.source_original_url+(source.source_original_url.endsWith('pdf')?'#page='+(source.page||1):'')};
    if(/^\/knowledge\/documents\/[0-9a-f]{64}\/file$/.test(source.original_url||''))return {label:kind(source)==='project_note'?'打开笔记快照':'打开解析文本快照',url:source.original_url};
    return null;
  }
  function publication(source){
    const meta=source.meta||{};
    if(/^https?:\/\//i.test(meta.url||''))return meta.url;
    if(/^10\.\d{4,9}\/\S+$/.test(meta.doi||''))return 'https://doi.org/'+encodeURIComponent(meta.doi).replace(/%2F/g,'/');
    return null;
  }
  function copyText(text,sources,session,origin){
    const ids=references(text).filter(id=>sources[id]);
    const number=id=>ids.includes(id)?'['+(ids.indexOf(id)+1)+']':'[引用未定位]';
    const prose=text.split(/(```[\s\S]*?```|`[^`]*`)/g).map((part,index)=>index%2?part:part
      .replace(/\[([^\]]+)\]\((k_[0-9a-f]{6,})\)/g,(_,label,id)=>label+number(id))
      .replace(/\[(k_[0-9a-f]{6,})\]|\b(k_[0-9a-f]{6,})\b/g,(_,id,bare)=>number(id||bare))).join('');
    if(!ids.length)return prose;
    const bibliography=ids.map((id,index)=>{
      const source=sources[id],file=original(source,session),url=file?origin+file.url:publication(source);
      return `[${index+1}] ${title(source)} · ${location(source)}（${typeLabel(source)}）`+(url?'\n'+url:'');
    });
    return prose+'\n\n来源\n'+bibliography.join('\n\n');
  }
  return {references,kind,typeLabel,location,title,excerpt,original,publication,copyText};
})();
if(typeof module!=='undefined')module.exports=SourceCitations;
