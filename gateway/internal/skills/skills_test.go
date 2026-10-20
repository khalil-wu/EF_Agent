package skills

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSplitFrontmatter(t *testing.T) {
	meta, body := SplitFrontmatter("---\nname: demo\ndescription: hello\nallowed-tools: [a, b]\n---\nBODY\n")
	if meta["name"] != "demo" || meta["description"] != "hello" {
		t.Fatalf("bad scalars: %v", meta)
	}
	if l, ok := meta["allowed-tools"].([]string); !ok || len(l) != 2 || l[1] != "b" {
		t.Fatalf("bad list: %v", meta["allowed-tools"])
	}
	if body != "BODY\n" {
		t.Fatalf("bad body %q", body)
	}
}

func TestLoadRepoSkills(t *testing.T) {
	dir := filepath.Join("..", "..", "..", "skills")
	if _, err := os.Stat(dir); err != nil {
		t.Skip("repo skills dir not present")
	}
	sk, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, s := range sk {
		names[s.Name] = true
		if len(s.Warnings) > 0 {
			t.Errorf("%s: %v", s.Name, s.Warnings)
		}
	}
	for _, want := range []string{"emotion_recognition", "data_understanding", "general_eeg_analysis"} {
		if !names[want] {
			t.Errorf("missing skill %s", want)
		}
	}
	idx := FormatIndex(sk)
	if idx == "" || !strings.Contains(idx, "<name>emotion_recognition</name>") {
		t.Fatalf("index missing skill: %s", idx)
	}
}

func TestVisibleToolsBounded(t *testing.T) {
	s := &Skill{Name: "x", AllowedTools: []string{"preprocess", "plot", "run_model"}}
	tools := VisibleTools(s)
	if len(tools) != len(BaseTools) { // registered inference and shared analysis tools are base-visible
		t.Fatalf("got %v", tools)
	}
	for i := 1; i < len(tools); i++ {
		if tools[i-1] > tools[i] {
			t.Fatalf("not sorted: %v", tools)
		}
	}
}

func TestFocusedToolExposureChangesActualToolSet(t *testing.T) {
	previousFocused, previousAll := FocusedTools, ExposeAllTools
	defer func() { FocusedTools, ExposeAllTools = previousFocused, previousAll }()
	FocusedTools, ExposeAllTools = true, false
	base := VisibleTools(nil)
	if len(base) != len(focusedBaseTools) || strings.Contains(strings.Join(base, ","), "plot") {
		t.Fatalf("focused base contains analysis tools: %v", base)
	}
	active := VisibleTools(&Skill{Name: "spectral", AllowedTools: []string{"preprocess", "segment_signal", "extract_features", "plot"}})
	if !strings.Contains(strings.Join(active, ","), "plot") || len(active) != len(base)+4 {
		t.Fatalf("active skill tools were not exposed: %v", active)
	}
	FocusedTools = false
	if len(VisibleTools(nil)) != len(BaseTools) {
		t.Fatal("shared default changed")
	}
}

func TestSkillReferenceReadsLinkedPathAndExistingShorthand(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "references"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "references", "api.md"), []byte("actual skill API"), 0o644); err != nil {
		t.Fatal(err)
	}
	skill := &Skill{Path: filepath.Join(root, "SKILL.md"), body: "main instructions"}
	for _, reference := range []string{"api.md", "references/api.md", `references\api.md`} {
		text, err := skill.Read(reference)
		if err != nil || text != "actual skill API" {
			t.Fatalf("reference %q did not resolve to the same API: %q %v", reference, text, err)
		}
	}
	if text, err := skill.Read(""); err != nil || text != "main instructions" {
		t.Fatalf("reading the main skill changed: %q %v", text, err)
	}
}

func TestSkillReferenceStaysInsideReferencesDirectory(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(root, "SKILL.md")
	if err := os.WriteFile(outside, []byte("outside references"), 0o644); err != nil {
		t.Fatal(err)
	}
	skill := &Skill{Path: outside}
	for _, reference := range []string{"../SKILL.md", "references/../SKILL.md", "references/../../outside.md", `..\SKILL.md`, `references\..\SKILL.md`, outside} {
		if text, err := skill.Read(reference); err == nil || os.IsNotExist(err) {
			t.Fatalf("reference %q escaped its directory or reached file lookup: %q %v", reference, text, err)
		}
	}
}
