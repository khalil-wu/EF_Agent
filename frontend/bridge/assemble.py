from pathlib import Path

path = Path(__file__).with_name('app.js')
source = path.read_text(encoding='utf-8')
controller = Path(__file__).with_name('stream-controller.js').read_text(encoding='utf-8')

def cut(start, end, replacement=''):
    global source
    a, b = source.index(start), source.index(end, source.index(start))
    source = source[:a] + replacement + source[b:]

source = source.replace('let transcriptTurn=0;\nconst expandedActivities=new Map();', 'let transcript,liveCursor=null;\nconst eventTraces=new Map();')
source = source.replace('let replaying=false;\n', '')
source = source.replace("if(j.running)watchSession(id,version,JSON.stringify(j));", 'if(j.running)watchSession(id,version);')
source = source.replace("function resetView(){transcriptTurn=0;renderInputRequest(null);$('#chat').innerHTML=emptyTpl;", 'function resetView(){renderInputRequest(null);transcript.reset();')
cut('function showError(', 'function setConn(')
cut('function renderSession(', 'const STEP_LABEL=', controller[:controller.index('async function send(')]+'\n')
cut('function toolStep(', '/* ---------- trace ---------- */')
cut('function inlineArtifact(', 'function openArtifact(')
cut('function renderTurnSources(', 'async function stopGeneration(')
cut('async function send(', 'const SKILL_NAMES=', controller[controller.index('async function send('):]+'\n')
source = source.replace("const emptyTpl=$('#empty').outerHTML;\n", '')
source = source.replace("function refreshIcons(root){const list=[...(root.matches?.('.ms')?[root]:[]),...root.querySelectorAll('.ms')];for(const el of list){el.dataset.icon=el.textContent.trim();el.setAttribute('aria-hidden','true')}", 'function refreshIcons(root){IconSystem.refresh(root);')
source = source.replace("if(c.target.matches?.('.ms'))refreshIcons(c.target);for(const node of c.addedNodes)if(node.nodeType===1)refreshIcons(node)", "if(c.target.closest?.('#chat'))continue;if(c.target.matches?.('.ms'))refreshIcons(c.target);for(const node of c.addedNodes)if(node.nodeType===1&&!node.closest('#chat'))refreshIcons(node)")
source = source.replace("const saved=new URLSearchParams", '''transcript=TranscriptUI.mount($('#chat'),{
  session:()=>sessionId,source:id=>knowledge[id],artifact:id=>artifacts[id],
  sourceTitle:SourceCitations.title,sourceLocation:SourceCitations.location,references:SourceCitations.references,
  artifactTitle,toolLabel:name=>TOOL_LABEL[name]||name,formatArgs:formatToolArgs,
  toolSummary:TranscriptProjection.toolSummary,toolVisible:TranscriptProjection.toolVisible,
  openArtifact,openTrace:()=>showTabAndOpen('trace'),
  copy:text=>navigator.clipboard.writeText(SourceCitations.copyText(text,knowledge,sessionId,location.origin)).then(()=>snack('已复制回复与引用')).catch(reportError),
  retry:()=>send({retry:true}).catch(reportError),changed:scroll
});
const saved=new URLSearchParams''')
path.write_text(source, encoding='utf-8')
print('Prepared shell bridge: React exclusively owns #chat; POST and restored GET use one SSE reducer.')
