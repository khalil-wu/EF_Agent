package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/worker"
)

func TestWorkspaceReferenceScopeReachesModelAndPresentationSurvivesRestart(t *testing.T) {
	var modelUser string
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Messages []llm.Message `json:"messages"`
		}
		json.NewDecoder(r.Body).Decode(&input)
		for _, message := range input.Messages {
			if message.Role == "user" {
				modelUser += message.Content + "\n"
				if message.Presentation != nil {
					t.Error("UI metadata leaked into provider message")
				}
			}
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"范围已收到。\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer model.Close()
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/workspaces/ws_abcdef123456/references" || r.URL.Query().Get("path") != "被试 01" {
			t.Error(r.URL.String())
		}
		json.NewEncoder(w).Encode(map[string]any{"items": []llm.InputReference{{WorkspaceID: "ws_abcdef123456", Path: "被试 01", Name: "被试 01", Kind: "directory"}}})
	}))
	defer backend.Close()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	storage := t.TempDir()
	s := New(cfg, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	s.sessions[id].WorkspaceID = "ws_abcdef123456"
	s.mu.Lock()
	s.publishLocked(s.sessions[id])
	s.mu.Unlock()
	body := `{"text":"只分析 @\"被试 01\" 的 EEG","display_text":"只分析 @\"被试 01\" 的 EEG","references":[{"workspace_id":"ws_abcdef123456","path":"被试 01","name":"伪造名称","kind":"file"}]}`
	w := request(t, h, "POST", "/sessions/"+id+"/messages", body)
	if w.Code != 200 || !strings.Contains(modelUser, `"path":"被试 01"`) || !strings.Contains(modelUser, `"kind":"directory"`) {
		t.Fatal(w.Code, modelUser, w.Body.String())
	}
	saved, err := session.Load(storage, id, nil)
	if err != nil {
		t.Fatal(err)
	}
	message := saved.Messages[0]
	if message.Presentation == nil || len(message.Presentation.References) != 1 || message.Presentation.References[0].Name != "被试 01" || strings.Contains(message.Presentation.Text, "file_references") {
		t.Fatalf("lost or spoofed reference: %+v", message)
	}
	for _, reference := range []string{`{"workspace_id":"ws_other","path":"."}`, `{"manifest_id":"sig_other_session"}`} {
		w = request(t, h, "POST", "/sessions/"+id+"/messages", `{"text":"分析","references":[`+reference+`]}`)
		if w.Code != 400 {
			t.Fatal("invalid scope accepted", w.Code)
		}
	}
}
