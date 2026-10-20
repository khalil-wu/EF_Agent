"""Skill catalog: scan ``skills/*/SKILL.md`` and expose the index that goes into the prompt.

Follows the Agent Skills convention used by pi and Codex: frontmatter must have
``name`` and ``description``; the body is loaded on demand by the model, not here.
Extra frontmatter keys (``modalities``, ``allowed-tools``, ``models``) are project-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_ALLOWED_TOOLS = 10


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    path: Path
    modalities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    disable_model_invocation: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    def body(self) -> str:
        """Return the SKILL.md body without frontmatter (loaded on activation)."""
        _, body = split_frontmatter(self.path.read_text(encoding="utf-8"))
        return body


def default_skills_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "skills"


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Minimal YAML-ish frontmatter parser: ``key: value`` and ``key: [a, b]`` only."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, object] = {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            items = [v.strip().strip("'\"") for v in raw[1:-1].split(",")]
            meta[key] = tuple(v for v in items if v)
        elif raw.lower() in {"true", "false"}:
            meta[key] = raw.lower() == "true"
        else:
            meta[key] = raw.strip("'\"")
    if end is None:
        return {}, text
    return meta, "\n".join(lines[end + 1 :])


def _validate(name: str, description: str, allowed_tools: tuple[str, ...]) -> list[str]:
    warnings: list[str] = []
    if len(name) > MAX_NAME_LENGTH:
        warnings.append(f"name exceeds {MAX_NAME_LENGTH} characters")
    if not all(c.islower() or c.isdigit() or c in "-_" for c in name):
        warnings.append("name must be lowercase letters, digits, hyphens or underscores")
    if not description:
        warnings.append("description is required")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        warnings.append(f"description exceeds {MAX_DESCRIPTION_LENGTH} characters")
    if len(allowed_tools) > MAX_ALLOWED_TOOLS:
        warnings.append(f"allowed-tools has {len(allowed_tools)} entries, limit is {MAX_ALLOWED_TOOLS}")
    return warnings


def load_skill(skill_md: Path) -> SkillSpec | None:
    meta, _ = split_frontmatter(skill_md.read_text(encoding="utf-8"))
    description = str(meta.get("description", "")).strip()
    if not description:
        return None
    name = str(meta.get("name") or skill_md.parent.name).strip()

    def as_tuple(key: str) -> tuple[str, ...]:
        value = meta.get(key, ())
        if isinstance(value, tuple):
            return value
        if isinstance(value, str) and value:
            return (value,)
        return ()

    allowed_tools = as_tuple("allowed-tools")
    return SkillSpec(
        name=name,
        description=description,
        path=skill_md,
        modalities=as_tuple("modalities"),
        allowed_tools=allowed_tools,
        models=as_tuple("models"),
        disable_model_invocation=bool(meta.get("disable-model-invocation", False)),
        warnings=tuple(_validate(name, description, allowed_tools)),
    )


def load_skills(skills_dir: Path | None = None) -> tuple[SkillSpec, ...]:
    """Discover skills: any directory containing SKILL.md, one level or deeper."""
    root = skills_dir or default_skills_dir()
    if not root.exists():
        return ()
    found: dict[str, SkillSpec] = {}
    for skill_md in sorted(root.rglob("SKILL.md")):
        spec = load_skill(skill_md)
        if spec is None or spec.name in found:
            continue
        found[spec.name] = spec
    return tuple(found.values())


def format_skills_for_prompt(skills: tuple[SkillSpec, ...]) -> str:
    """XML index in the agentskills.io format (same shape pi injects into the system prompt)."""
    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return ""
    lines = [
        "The following skills provide specialized instructions for specific tasks.",
        "Call read_skill with the skill name to load its full instructions when the task matches its description.",
        "",
        "<available_skills>",
    ]
    for s in visible:
        lines.append("  <skill>")
        lines.append(f"    <name>{escape(s.name)}</name>")
        lines.append(f"    <description>{escape(s.description)}</description>")
        if s.modalities:
            lines.append(f"    <modalities>{escape(', '.join(s.modalities))}</modalities>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


# Must mirror gateway/internal/skills/skills.go BaseTools (the runtime authority): every registered
# tool is base-visible; a skill's allowed-tools are recommendations, not visibility gates.
BASE_TOOLS: tuple[str, ...] = (
    "analyze_signal",
    "workspace_quality",
    "workspace_files",
    "list_files",
    "read_file",
    "write_file",
    "edit_file",
    "save_to_workspace",
    "read_document",
    "inspect_record",
    "check_model_compat",
    "list_models",
    "route_model",
    "run_model",
    "recognize_emotion",
    "update_record_metadata",
    "device_lookup",
    "preprocess",
    "clean_artifacts",
    "segment_signal",
    "extract_features",
    "save_emotion_assessment",
    "read_artifact",
    "plot",
    "export",
    "run_python",
    "search_knowledge",
    "knowledge_graph",
    "detect_transients",
)


def visible_tools(active: SkillSpec | None) -> tuple[str, ...]:
    """Base tools plus the active skill's allowed-tools, deduplicated, order preserved."""
    seen: list[str] = list(BASE_TOOLS)
    if active is not None:
        for tool in active.allowed_tools:
            if tool not in seen:
                seen.append(tool)
    return tuple(seen)
