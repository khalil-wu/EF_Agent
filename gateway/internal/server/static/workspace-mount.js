/* Mount a local folder from the left workspace navigation. */
const WorkspaceMount=(()=>{
  function mount({api,select}){
    async function openMount(){
      const record=await api('/workspaces/pick',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(!record.cancelled)await select(record.workspace_id);
    }
    return {openMount};
  }
  return {mount};
})();
