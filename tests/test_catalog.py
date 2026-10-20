from pathlib import Path
import re

from eegagent.skills.catalog import (
    BASE_TOOLS,
    MAX_ALLOWED_TOOLS,
    default_skills_dir,
    format_skills_for_prompt,
    load_skills,
    split_frontmatter,
    visible_tools,
)


def test_split_frontmatter_parses_scalars_and_lists():
    text = "---\nname: demo\ndescription: hello world\nallowed-tools: [a, b, c]\ndisable-model-invocation: true\n---\n# body\n"
    meta, body = split_frontmatter(text)
    assert meta["name"] == "demo"
    assert meta["description"] == "hello world"
    assert meta["allowed-tools"] == ("a", "b", "c")
    assert meta["disable-model-invocation"] is True
    assert body.strip() == "# body"


def test_repo_skills_load_and_respect_tool_limit():
    skills = load_skills(default_skills_dir())
    names = {s.name for s in skills}
    assert {"emotion_recognition", "data_understanding", "general_eeg_analysis"} <= names
    for s in skills:
        assert s.description
        assert len(s.allowed_tools) <= MAX_ALLOWED_TOOLS, s.name
        assert not s.warnings, (s.name, s.warnings)


def test_prompt_index_has_only_name_description_modalities():
    skills = load_skills(default_skills_dir())
    xml = format_skills_for_prompt(skills)
    assert "<available_skills>" in xml
    assert "<name>emotion_recognition</name>" in xml
    assert "allowed-tools" not in xml  # body-level details are loaded on activation, not indexed


def test_visible_tools_is_base_plus_active_skill():
    skills = {s.name: s for s in load_skills(default_skills_dir())}
    assert visible_tools(None) == BASE_TOOLS
    active = skills["emotion_recognition"]
    tools = visible_tools(active)
    assert tools[: len(BASE_TOOLS)] == BASE_TOOLS
    assert set(active.allowed_tools) <= set(tools)
    assert len(tools) <= len(BASE_TOOLS) + MAX_ALLOWED_TOOLS


def test_skill_body_is_loaded_on_demand(tmp_path: Path):
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nBODY\n", encoding="utf-8")
    (skills,) = load_skills(tmp_path)
    assert skills.body().strip() == "BODY"


def test_base_tools_match_gateway_runtime_catalog():
    root = Path(__file__).resolve().parents[1]
    gateway = (root / "gateway/internal/skills/skills.go").read_text(encoding="utf-8")
    declaration = re.search(r"var BaseTools = \[\]string\{([^}]+)\}", gateway)
    assert declaration is not None
    gateway_names = re.findall(r'"([a-z_]+)"', declaration.group(1))
    assert set(BASE_TOOLS) == set(gateway_names)
    assert len(BASE_TOOLS) == len(set(BASE_TOOLS))


def test_repo_skill_recommendations_resolve_to_base_tools():
    for skill in load_skills(default_skills_dir()):
        assert set(skill.allowed_tools) <= set(BASE_TOOLS), skill.name


def test_repo_skill_on_demand_references_exist():
    skills = {skill.name: skill for skill in load_skills(default_skills_dir())}
    calls = re.compile(r"read_skill\(name=([a-z_]+), reference=([a-z_.-]+)\)")
    for path in default_skills_dir().rglob("*.md"):
        for name, reference in calls.findall(path.read_text(encoding="utf-8")):
            assert name in skills, (path, name)
            assert (skills[name].base_dir / "references" / reference).is_file(), (path, name, reference)
