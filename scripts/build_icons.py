"""Bundle the local Lucide SVG subset used by EFAgent.

npm pack lucide-static@1.51.0 --pack-destination tmp/icon-build --silent
python scripts/build_icons.py tmp/icon-build/lucide-static-1.51.0.tgz
"""

import json
from pathlib import Path
import re
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "gateway/internal/server/static"
VERSION = "1.51.0"

# Existing UI tokens are retained while their shapes come from one icon family.
# EEG signals use activity; brain-specific entry points use brain/brain-circuit.
MAPPING = {
    "add": "plus",
    "arrow_downward": "arrow-down",
    "arrow_right_alt": "arrow-right",
    "arrow_upward": "arrow-up",
    "article": "newspaper",
    "auto_fix_high": "wand-sparkles",
    "bedtime": "moon-star",
    "block": "ban",
    "chat_bubble": "message-circle",
    "check_circle": "circle-check",
    "chevron_right": "chevron-right",
    "close": "x",
    "compress": "minimize-2",
    "content_copy": "copy",
    "dark_mode": "moon",
    "delete_sweep": "trash-2",
    "description": "file-text",
    "download": "download",
    "edit_square": "square-pen",
    "error": "circle-alert",
    "expand_more": "chevron-down",
    "extension": "puzzle",
    "folder_open": "folder-open",
    "grid_on": "grid-2x2",
    "hub": "network",
    "image": "image",
    "info": "info",
    "insights": "chart-no-axes-combined",
    "inventory_2": "archive",
    "light_mode": "sun",
    "menu": "menu",
    "menu_book": "book-open",
    "monitor_heart": "heart-pulse",
    "more_horiz": "ellipsis",
    "neurology": "brain",
    "open_in_new": "external-link",
    "person": "user-round",
    "progress_activity": "loader-circle",
    "psychology": "brain-circuit",
    "refresh": "rotate-cw",
    "reply": "undo-2",
    "rule": "list-checks",
    "search": "search",
    "show_chart": "chart-line",
    "stacked_line_chart": "chart-area",
    "stop": "square",
    "swap_horiz": "arrow-left-right",
    "table_view": "table-2",
    "terminal": "terminal",
    "troubleshoot": "scan-search",
    "tune": "sliders-horizontal",
    "unfold_more": "chevrons-up-down",
    "videocam": "video",
    "view_sidebar": "panel-left",
    "view_week": "columns-3",
    "warning": "triangle-alert",
    "waves": "activity",
    # Direction-specific controls and tool categories for the transcript UI.
    "panel_right": "panel-right",
    "check": "check",
    "chevron_left": "chevron-left",
    "chevron_up": "chevron-up",
    "settings": "settings-2",
    "upload_file": "file-up",
    "code": "code-2",
    "science": "flask-conical",
    "search_file": "file-search",
    "database": "database",
    "folder": "folder",
    "play_arrow": "play",
    "pause": "pause",
    "keyboard": "keyboard",
    "help": "circle-help",
    "schedule": "clock-3",
}


def build(archive_path):
    with tarfile.open(archive_path) as archive:
        shapes = {}
        for name in sorted(set(MAPPING.values())):
            source = archive.extractfile(f"package/icons/{name}.svg").read().decode()
            body = re.search(r"<svg\b[^>]*>(.*?)</svg>", source, re.S).group(1)
            shapes[name] = [re.sub(r"\s+", " ", shape).strip() for shape in re.findall(r"<[^>]+/>", body)]
        license_text = archive.extractfile("package/LICENSE").read().decode()
    used = set()
    for file in ("app.js", "index.html"):
        source = (STATIC / file).read_text(encoding="utf-8")
        used.update(re.findall(r'<span[^>]*class=["\'][^"\']*\bms\b[^"\']*["\'][^>]*>\s*([a-z][a-z0-9_]*)\s*</span>', source))
    missing = used - MAPPING.keys()
    if missing:
        raise ValueError(f"Unmapped UI icon tokens: {sorted(missing)}")
    script = """/* Generated from lucide-static vVERSION (ISC); see lucide-LICENSE.txt.
 * Run scripts/build_icons.py to change the locally bundled icon subset. */
const IconSystem=(()=>{
  const names=MAPPING;
  const shapes=SHAPES;
  function svg(token){
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${shapes[names[token]].join('')}</svg>`;
  }
  function set(element,token){
    element.dataset.icon=token;
    element.setAttribute('aria-hidden','true');
    element.innerHTML=svg(token);
  }
  function refresh(root){
    const elements=[...(root.matches?.('.ms')?[root]:[]),...root.querySelectorAll('.ms')];
    for(const element of elements){
      if(element.firstElementChild?.tagName==='svg')continue;
      set(element,element.textContent.trim());
    }
  }
  return {names,svg,set,refresh};
})();
if(typeof module!=='undefined')module.exports=IconSystem;
"""
    script = script.replace("VERSION", VERSION).replace("MAPPING", json.dumps(MAPPING, separators=(",", ":"))).replace("SHAPES", json.dumps(shapes, separators=(",", ":")))
    (STATIC / "icons.js").write_text(script, encoding="utf-8", newline="\n")
    (STATIC / "lucide-LICENSE.txt").write_text(license_text, encoding="utf-8", newline="\n")
    print(f"Bundled {len(MAPPING)} UI tokens from {len(shapes)} Lucide icons ({VERSION}).")


if __name__ == "__main__":
    build(sys.argv[1])
