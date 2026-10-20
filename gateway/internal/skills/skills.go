// Package skills loads SKILL.md files and formats the index that goes into the system prompt.
// Same conventions as pi's core/skills.ts: frontmatter needs name + description, body is loaded on demand.
package skills

import (
	"fmt"
	"html"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const MaxAllowedTools = 10

// BaseTools lists every registered worker tool: all tools stay visible regardless of the active
// skill. Skills contribute knowledge and recommended tools (allowed-tools), not visibility gates.
// Keep in sync with src/eegagent/skills/catalog.py BASE_TOOLS.
var BaseTools = []string{"analyze_signal", "workspace_quality", "workspace_files", "list_files", "read_file", "write_file", "edit_file", "save_to_workspace", "read_document", "inspect_record", "check_model_compat", "list_models", "route_model", "run_model", "recognize_emotion", "update_record_metadata", "device_lookup", "preprocess", "clean_artifacts", "segment_signal", "extract_features", "save_emotion_assessment", "read_artifact", "plot", "export", "run_python", "search_knowledge", "knowledge_graph", "detect_transients"}
var FocusedTools = false
var focusedBaseTools = []string{"analyze_signal", "workspace_quality", "workspace_files", "list_files", "read_file", "write_file", "edit_file", "save_to_workspace", "read_document", "inspect_record", "recognize_emotion", "update_record_metadata", "read_artifact", "run_python", "search_knowledge"}

// ExposeAllTools selects the shared registry interface; this is the production control condition.
var ExposeAllTools = false

// AllToolNames is filled by the gateway at startup (used only when ExposeAllTools is set).
var AllToolNames []string

type Skill struct {
	Name                   string
	Description            string
	Path                   string
	Modalities             []string
	AllowedTools           []string
	Models                 []string
	DisableModelInvocation bool
	Warnings               []string
	body                   string
}

// Body returns the SKILL.md content after the frontmatter (what gets injected on activation).
func (s *Skill) Body() string { return s.body }

func (s *Skill) Read(reference string) (string, error) {
	if reference == "" {
		return s.body, nil
	}
	// SKILL.md links use references/api.md; the tool also accepts the existing api.md shorthand.
	reference = filepath.FromSlash(strings.TrimPrefix(strings.ReplaceAll(reference, "\\", "/"), "references/"))
	if !filepath.IsLocal(reference) {
		return "", fmt.Errorf("reference 必须位于 skill 的 references 目录")
	}
	text, err := os.ReadFile(filepath.Join(filepath.Dir(s.Path), "references", reference))
	return string(text), err
}

// Load scans dir recursively for SKILL.md files.
func Load(dir string) ([]*Skill, error) {
	var out []*Skill
	seen := map[string]bool{}
	err := filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || d.Name() != "SKILL.md" {
			return nil
		}
		s, err := parseFile(p)
		if err != nil || s == nil {
			return err
		}
		if seen[s.Name] {
			return nil
		}
		seen[s.Name] = true
		out = append(out, s)
		return nil
	})
	if os.IsNotExist(err) {
		return nil, nil
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, err
}

func parseFile(path string) (*Skill, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	meta, body := SplitFrontmatter(string(raw))
	desc := strings.TrimSpace(scalar(meta["description"]))
	if desc == "" {
		return nil, nil
	}
	name := strings.TrimSpace(scalar(meta["name"]))
	if name == "" {
		name = filepath.Base(filepath.Dir(path))
	}
	s := &Skill{
		Name:                   name,
		Description:            desc,
		Path:                   path,
		Modalities:             list(meta["modalities"]),
		AllowedTools:           list(meta["allowed-tools"]),
		Models:                 list(meta["models"]),
		DisableModelInvocation: scalar(meta["disable-model-invocation"]) == "true",
		body:                   body,
	}
	if len(s.AllowedTools) > MaxAllowedTools {
		s.Warnings = append(s.Warnings, fmt.Sprintf("allowed-tools has %d entries, limit is %d", len(s.AllowedTools), MaxAllowedTools))
	}
	if len(desc) > 1024 {
		s.Warnings = append(s.Warnings, "description exceeds 1024 characters")
	}
	return s, nil
}

// SplitFrontmatter parses the minimal "key: value" / "key: [a, b]" frontmatter used by SKILL.md.
func SplitFrontmatter(text string) (map[string]any, string) {
	lines := strings.Split(text, "\n")
	if len(lines) == 0 || strings.TrimSpace(lines[0]) != "---" {
		return map[string]any{}, text
	}
	meta := map[string]any{}
	end := -1
	for i := 1; i < len(lines); i++ {
		l := lines[i]
		if strings.TrimSpace(l) == "---" {
			end = i
			break
		}
		k, v, ok := strings.Cut(l, ":")
		if !ok || strings.TrimSpace(k) == "" || strings.HasPrefix(strings.TrimSpace(l), "#") {
			continue
		}
		k = strings.TrimSpace(k)
		v = strings.TrimSpace(v)
		if strings.HasPrefix(v, "[") && strings.HasSuffix(v, "]") {
			var items []string
			for _, it := range strings.Split(v[1:len(v)-1], ",") {
				it = strings.Trim(strings.TrimSpace(it), `"'`)
				if it != "" {
					items = append(items, it)
				}
			}
			meta[k] = items
		} else {
			meta[k] = strings.Trim(v, `"'`)
		}
	}
	if end < 0 {
		return map[string]any{}, text
	}
	return meta, strings.Join(lines[end+1:], "\n")
}

func scalar(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func list(v any) []string {
	switch t := v.(type) {
	case []string:
		return t
	case string:
		if t != "" {
			return []string{t}
		}
	}
	return nil
}

// FormatIndex renders the <available_skills> block (agentskills.io shape, same as pi).
func FormatIndex(sk []*Skill) string {
	var b strings.Builder
	n := 0
	for _, s := range sk {
		if !s.DisableModelInvocation {
			n++
		}
	}
	if n == 0 {
		return ""
	}
	b.WriteString("The following skills provide specialized instructions for specific tasks.\n")
	b.WriteString("Call read_skill with the skill name to load its full instructions when the task matches its description.\n\n")
	b.WriteString("<available_skills>\n")
	for _, s := range sk {
		if s.DisableModelInvocation {
			continue
		}
		b.WriteString("  <skill>\n")
		fmt.Fprintf(&b, "    <name>%s</name>\n", html.EscapeString(s.Name))
		fmt.Fprintf(&b, "    <description>%s</description>\n", html.EscapeString(s.Description))
		if len(s.Modalities) > 0 {
			fmt.Fprintf(&b, "    <modalities>%s</modalities>\n", html.EscapeString(strings.Join(s.Modalities, ", ")))
		}
		b.WriteString("  </skill>\n")
	}
	b.WriteString("</available_skills>")
	return b.String()
}

// VisibleTools returns BaseTools plus the active skill's allowed-tools, deduplicated, sorted for cache stability.
func VisibleTools(active *Skill) []string {
	set := map[string]bool{}
	var out []string
	add := func(t string) {
		if !set[t] {
			set[t] = true
			out = append(out, t)
		}
	}
	if ExposeAllTools {
		for _, t := range AllToolNames {
			add(t)
		}
	}
	base := BaseTools
	if FocusedTools && !ExposeAllTools {
		base = focusedBaseTools
	}
	for _, t := range base {
		add(t)
	}
	if active != nil {
		for _, t := range active.AllowedTools {
			add(t)
		}
	}
	sort.Strings(out)
	return out
}
