/* Explicit file/folder context selected from the active workspace or this conversation. */
const FileMentions=(()=>{
  const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const identity=item=>item.manifest_id||item.workspace_id+':'+item.path;
  const label=item=>item.path==='.'?'当前文件夹':item.name;
  function queryAt(text,caret){
    const match=text.slice(0,caret).match(/(^|[\s（(,:，：])@("[^"]*"?|[^\s@"]*)$/u);
    if(!match)return null;
    const raw=match[2],query=raw.startsWith('"')?raw.slice(1,raw.endsWith('"')&&raw.length>1?-1:undefined):raw;
    const start=match.index+match[1].length;
    const token=text.slice(start+1).match(raw.startsWith('"')?/^"[^"]*(?:"|$)/:/^[^\s@"]*/)[0];
    return {start,end:start+1+token.length,query};
  }
  function mount({input,menu,chips,list,scope,preview,changed}){
    let selected=[],items=[],index=0,query=null,requestKey='',suppressed='',revision=0,timer=null,chipKey='';
    const drafts=new Map();let activeDraft='';
    const icon=name=>`<span class="ms" aria-hidden="true">${IconSystem.svg(name)}</span>`;
    function hide(){menu.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant')}
    function renderChips(){
      const key=selected.map(item=>identity(item)).join('|');if(key===chipKey)return;chipKey=key;
      chips.hidden=!selected.length;chips.innerHTML=selected.map((item,i)=>`<span class="reference-chip"><button type="button" data-reference-preview="${i}" title="预览 ${esc(item.path||item.name)}">${icon(item.kind==='directory'?'folder_open':'description')}<span>${esc(label(item))}</span></button><button type="button" data-reference-remove="${i}" aria-label="移除引用 ${esc(label(item))}" title="移除引用">${icon('close')}</button></span>`).join('');
    }
    function renderMenu(){
      menu.hidden=false;input.setAttribute('aria-expanded','true');
      menu.innerHTML='<div class="mention-heading">引用文件或文件夹</div>'+items.map((item,i)=>`<button type="button" class="mention-option" role="option" id="mention-${i}" aria-selected="${i===index}" title="${esc(item.path||item.name)}">${icon(item.kind==='directory'?'folder_open':'description')}<span class="mention-copy"><strong>${esc(label(item))}</strong><small>${esc(item.manifest_id?'本次会话':item.path==='.'?'整个当前工作区':item.path.split('/').slice(0,-1).join('/')||'当前文件夹')}</small></span>${item.excluded?'<span class="mention-excluded">已排除分析</span>':''}${item.kind==='directory'?`<span class="mention-drill" data-mention-drill="${i}" title="打开文件夹">${icon('chevron_right')}</span>`:''}</button>`).join('')+(!items.length?'<div class="mention-empty">没有匹配的文件。可从左侧挂接文件夹，或先上传文件。</div>':'<div class="mention-help">↑ ↓ 选择 · Enter 引用 · → 打开文件夹 · Esc 关闭</div>');
      items.forEach((item,i)=>{const button=menu.querySelector('#mention-'+i);button.onmousedown=e=>e.preventDefault();button.onclick=e=>e.target.closest('[data-mention-drill]')?browse(item):choose(i)});
      if(items.length)input.setAttribute('aria-activedescendant','mention-'+index);else input.removeAttribute('aria-activedescendant');
    }
    function update(){
      renderChips();
      if(input.ownerDocument.activeElement!==input){revision++;clearTimeout(timer);requestKey='';hide();return}
      query=queryAt(input.value,input.selectionStart);
      if(!query){requestKey='';revision++;clearTimeout(timer);hide();return}
      const key=scope()+'|'+query.start+'|'+query.query;
      if(key===suppressed){hide();return}if(key===requestKey){input.setAttribute('aria-expanded',String(!menu.hidden));if(!menu.hidden&&items.length)input.setAttribute('aria-activedescendant','mention-'+index);return}
      requestKey=key;const request=++revision;clearTimeout(timer);index=0;items=[];
      menu.hidden=false;menu.innerHTML='<div class="mention-empty" role="status">正在查找文件…</div>';input.setAttribute('aria-expanded','true');input.removeAttribute('aria-activedescendant');
      timer=setTimeout(async()=>{
        try{const result=await list(query.query);if(request!==revision)return;items=result;renderMenu()}
        catch(error){if(request!==revision)return;items=[];menu.innerHTML=`<div class="mention-empty" role="alert">${esc(error.message)}</div>`}
      },100);
    }
    function choose(i){
      const item=items[i],end=query.end+(input.value[query.end]===' '?1:0);input.setRangeText('',query.start,end,'end');
      if(!selected.some(existing=>identity(existing)===identity(item)))selected.push(item);
      revision++;requestKey='';hide();renderChips();input.focus();changed();
    }
    function browse(item){
      const path=item.path==='.'?'./':item.path+'/';input.setRangeText('@'+(/[\s]/.test(path)?'"'+path:path),query.start,query.end,'end');input.focus();requestKey='';update();
    }
    chips.onclick=e=>{
      const open=e.target.closest('[data-reference-preview]');if(open){preview(selected[Number(open.dataset.referencePreview)]);return}
      const remove=e.target.closest('[data-reference-remove]');if(remove){const item=selected[Number(remove.dataset.referenceRemove)];selected=selected.filter(other=>identity(other)!==identity(item));renderChips();changed();input.focus()}
    };
    input.addEventListener('click',update);input.addEventListener('focus',update);input.addEventListener('keyup',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key))update()});input.addEventListener('blur',()=>{revision++;clearTimeout(timer);requestKey='';hide()});
    return {update,
      handleKey(e){
        if(menu.hidden||e.isComposing)return false;
        if(e.key==='Escape'){e.preventDefault();e.stopPropagation();suppressed=requestKey;revision++;clearTimeout(timer);hide();return true}
        if(['ArrowDown','ArrowUp'].includes(e.key)&&items.length){e.preventDefault();index=(index+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;renderMenu();menu.querySelector('#mention-'+index).scrollIntoView({block:'nearest'});return true}
        if(e.key==='ArrowRight'&&items[index]?.kind==='directory'){e.preventDefault();browse(items[index]);return true}
        if((e.key==='Enter'&&!e.shiftKey||e.key==='Tab')&&items.length){e.preventDefault();choose(index);return true}if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();return true}return false;
      },
      references(){return [...selected]},
      clear(submitted=selected){const sent=new Set(submitted.map(identity));selected=selected.filter(item=>!sent.has(identity(item)));drafts.delete(activeDraft);renderChips();requestKey='';revision++;hide()},
      removeWorkspace(id){selected=selected.filter(item=>item.workspace_id!==id);for(const draft of drafts.values())draft.selected=draft.selected.filter(item=>item.workspace_id!==id);renderChips();requestKey='';revision++;clearTimeout(timer);hide()},
      save(id){drafts.set(id,{text:input.value,selected:[...selected]})},
      restore(id,preserve=false){if(preserve)drafts.delete(activeDraft);else{const draft=drafts.get(id);input.value=draft?.text||'';selected=draft?[...draft.selected]:[]}activeDraft=id;requestKey='';suppressed='';revision++;clearTimeout(timer);hide();renderChips()},
    };
  }
  return {mount,queryAt};
})();
if(typeof module!=='undefined')module.exports=FileMentions;
