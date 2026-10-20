package prompt

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func saveWorkspace(t *testing.T, storage, id, root string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(storage, "workspaces"), 0o755); err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(map[string]any{"name": id, "root": root, "excluded": []string{"excluded.npz"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(storage, "workspaces", id+".json"), body, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestEFAgentProtocolTakesPriorityOverLegacyNames(t *testing.T) {
	root, storage := t.TempDir(), t.TempDir()
	saveWorkspace(t, storage, "ws_brand", root)
	for _, name := range []string{"EFAGENT.md", "EEGAGENT.md", "AGENTS.md"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("protocol "+name), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	files := LoadProjectContext([]string{root})
	if len(files) != 1 || !strings.HasSuffix(files[0].Path, "/EFAGENT.md") {
		t.Fatalf("project context: %+v", files)
	}
	context := WorkspaceContext(storage, "ws_brand")
	if !strings.Contains(context, "protocol EFAGENT.md") || strings.Contains(context, "protocol EEGAGENT.md") || strings.Contains(context, "protocol AGENTS.md") {
		t.Fatal(context)
	}
}

func TestWorkspaceProtocolFollowsCurrentSessionAndFileChanges(t *testing.T) {
	storage, first, second := t.TempDir(), t.TempDir(), t.TempDir()
	saveWorkspace(t, storage, "ws_first", first)
	saveWorkspace(t, storage, "ws_second", second)
	for path, text := range map[string]string{
		filepath.Join(first, "EEGAGENT.md"): "采集协议 A：采样率 128 Hz。",
		filepath.Join(first, "AGENTS.md"):   "未采用的通用说明。",
		filepath.Join(second, "AGENTS.md"):  "采集协议 B：采样率 256 Hz。",
	} {
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	firstContext := WorkspaceContext(storage, "ws_first")
	if !strings.Contains(firstContext, "协议 A") || !strings.Contains(firstContext, filepath.ToSlash(first)) || !strings.Contains(firstContext, "excluded.npz") || strings.Contains(firstContext, "未采用的通用说明") {
		t.Fatal(firstContext)
	}
	secondContext := WorkspaceContext(storage, "ws_second")
	if !strings.Contains(secondContext, "协议 B") || strings.Contains(secondContext, "协议 A") || strings.Contains(secondContext, filepath.ToSlash(first)) {
		t.Fatalf("another session inherited the previous workspace: %s", secondContext)
	}
	if err := os.WriteFile(filepath.Join(first, "EEGAGENT.md"), []byte("采集协议 A 已更正：采样率 512 Hz。"), 0o644); err != nil {
		t.Fatal(err)
	}
	updated := WorkspaceContext(storage, "ws_first")
	if !strings.Contains(updated, "512 Hz") || strings.Contains(updated, "128 Hz") {
		t.Fatalf("workspace protocol edit was not read on the next request: %s", updated)
	}
}

func TestWorkspaceProtocolReadFailureHasSourceAndDoesNotUseAnotherProtocol(t *testing.T) {
	storage, root := t.TempDir(), t.TempDir()
	saveWorkspace(t, storage, "ws_broken", root)
	broken := filepath.Join(root, "EEGAGENT.md")
	if err := os.Mkdir(broken, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "AGENTS.md"), []byte("another protocol"), 0o644); err != nil {
		t.Fatal(err)
	}
	context := WorkspaceContext(storage, "ws_broken")
	if !strings.Contains(context, "工作区协议读取失败") || !strings.Contains(context, filepath.ToSlash(broken)) || strings.Contains(context, "another protocol") {
		t.Fatal(context)
	}
}

func TestWorkspaceWithoutProtocolStillHasUsableScope(t *testing.T) {
	storage, root := t.TempDir(), t.TempDir()
	saveWorkspace(t, storage, "ws_plain", root)
	context := WorkspaceContext(storage, "ws_plain")
	if !strings.Contains(context, "root="+root) || !strings.Contains(context, "excluded.npz") || strings.Contains(context, "<workspace_instructions ") {
		t.Fatal(context)
	}
	if WorkspaceContext(storage, "") != "" {
		t.Fatal("projectless session has a workspace protocol")
	}
	if broken := WorkspaceContext(storage, "ws_missing"); !strings.Contains(broken, "工作区记录读取失败") || !strings.Contains(broken, "ws_missing") {
		t.Fatal(broken)
	}
}
