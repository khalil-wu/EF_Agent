package session

import (
	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestCitationSnapshotAndAssistantMetadataSurviveRestart(t *testing.T) {
	dir := t.TempDir()
	s := &agent.Session{ID: "ses_abcdef123456", Messages: []llm.Message{{Role: "assistant", Content: "Answer [k_abcdef].", Phase: "final_answer", CitationIDs: []string{"k_abcdef"}}}}
	s.RememberSources([]json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef","text":"first version","document_url":"/knowledge/documents/version/file","original_url":"https://example.org/paper"}`)})
	s.Knowledge = []json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef","text":"current version"}`)}
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	loaded, err := Load(dir, s.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	var source struct {
		Text        string `json:"text"`
		DocumentURL string `json:"document_url"`
		OriginalURL string `json:"original_url"`
	}
	if err := json.Unmarshal(loaded.Sources["k_abcdef"], &source); err != nil {
		t.Fatal(err)
	}
	if source.Text != "first version" || source.DocumentURL != "/knowledge/documents/version/file" || source.OriginalURL != "https://example.org/paper" || loaded.Messages[0].Phase != "final_answer" || len(loaded.Messages[0].CitationIDs) != 1 || loaded.Messages[0].CitationIDs[0] != "k_abcdef" {
		t.Fatalf("citation replay metadata changed: %+v", loaded)
	}
}

func TestLegacySourcesRecoverFromArchivedToolsAndDirectDocumentReads(t *testing.T) {
	dir := t.TempDir()
	s := &agent.Session{ID: "ses_123456abcdef", ArchivedMessages: []llm.Message{{Role: "tool", Name: "search_knowledge", Content: `{"chunks":[{"chunk_id":"k_abcdef","text":"archived first version"}]}`}}, Messages: []llm.Message{{Role: "tool", Name: "read_document", Content: `{"chunks":[{"chunk_id":"k_123456","text":"read excerpt","page":4},{"chunk_id":"k_abcdef","text":"later version"}]}`}}, Knowledge: []json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef","text":"latest retrieval"}`)}}
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	loaded, err := Load(dir, s.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	var archived, read struct {
		Text string `json:"text"`
		Page int    `json:"page"`
	}
	if err := json.Unmarshal(loaded.Sources["k_abcdef"], &archived); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(loaded.Sources["k_123456"], &read); err != nil {
		t.Fatal(err)
	}
	if len(loaded.Sources) != 2 || archived.Text != "archived first version" || read.Text != "read excerpt" || read.Page != 4 {
		t.Fatalf("legacy evidence recovery changed history: %v", loaded.Sources)
	}
}

func TestEvidenceSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	s := &agent.Session{ID: "ses_abcdef123456", Previews: map[string]json.RawMessage{"sig_abc": json.RawMessage(`{"artifact_id":"art_abcdef"}`)}, Knowledge: []json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef"}`)}, KnowledgeFacts: []json.RawMessage{json.RawMessage(`{"fact_id":"fact_1"}`)}, LastError: "upstream unavailable"}
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	out, err := Load(dir, s.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(out.Previews) != 1 || len(out.Knowledge) != 1 || len(out.KnowledgeFacts) != 1 || out.LastError != s.LastError {
		t.Fatalf("evidence lost: %+v", out)
	}
}

func TestUsageAndMetadataIndex(t *testing.T) {
	dir := t.TempDir()
	cached := 64
	s := &agent.Session{ID: "ses_123456abcdef", Title: "长分析", Usage: []agent.TurnUsage{{Turn: 1, PromptTokens: 100, CachedTokens: &cached, Completed: true}}}
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	loaded, err := Load(dir, s.ID, nil)
	if err != nil || len(loaded.Usage) != 1 || *loaded.Usage[0].CachedTokens != 64 {
		t.Fatalf("usage lost: %+v %v", loaded, err)
	}
	if err := os.WriteFile(filepath.Join(dir, "sessions", s.ID+".json"), []byte("large transcript unavailable"), 0o644); err != nil {
		t.Fatal(err)
	}
	list, err := List(dir)
	if err != nil || len(list) != 1 || list[0].Title != "长分析" {
		t.Fatalf("metadata listing read full transcript: %+v %v", list, err)
	}
}

func TestWorkspaceSurvivesRestartAndLegacyMetadataMigration(t *testing.T) {
	dir := t.TempDir()
	s := &agent.Session{ID: "ses_abcdef123456", Title: "工作区分析", WorkspaceID: "ws_abcdef123456"}
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	loaded, err := Load(dir, s.ID, nil)
	if err != nil || loaded.WorkspaceID != s.WorkspaceID {
		t.Fatalf("workspace lost on restart: %+v %v", loaded, err)
	}
	metaPath := filepath.Join(dir, "sessions", s.ID+".meta.json")
	if err := os.WriteFile(metaPath, []byte(`{"session_id":"ses_abcdef123456","title":"旧索引"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	list, err := List(dir)
	if err != nil || len(list) != 1 || list[0].WorkspaceID != s.WorkspaceID || list[0].Title != s.Title {
		t.Fatalf("legacy index did not recover workspace: %+v %v", list, err)
	}
	if err := os.WriteFile(filepath.Join(dir, "sessions", s.ID+".json"), []byte("full transcript unavailable"), 0o644); err != nil {
		t.Fatal(err)
	}
	list, err = List(dir)
	if err != nil || len(list) != 1 || list[0].WorkspaceID != s.WorkspaceID {
		t.Fatalf("migrated index was not persisted: %+v %v", list, err)
	}
}
