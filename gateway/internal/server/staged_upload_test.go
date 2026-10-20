package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/worker"
)

func uploadDraft(t *testing.T, handler http.Handler, id, filename string, staged bool) string {
	t.Helper()
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	file, _ := form.CreateFormFile("file", filename)
	_, _ = file.Write([]byte("explicit transport fixture"))
	if staged {
		_ = form.WriteField("staged", "true")
	}
	_ = form.Close()
	r := httptest.NewRequest("POST", "/sessions/"+id+"/attachments", &body)
	r.Header.Set("Content-Type", form.FormDataContentType())
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var result struct {
		Manifest struct {
			ID string `json:"manifest_id"`
		} `json:"manifest"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	return result.Manifest.ID
}

func TestPreparedAttachmentsStayOutOfContextUntilExplicitMessageCommit(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Args struct {
				Path string `json:"path"`
			} `json:"args"`
		}
		_ = json.NewDecoder(r.Body).Decode(&input)
		name := strings.TrimSuffix(filepath.Base(input.Args.Path), ".npy")
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "manifest": map[string]any{
			"manifest_id": "sig_" + name, "modality": "eeg", "attachment_sha256": "sha_" + name, "source_path": input.Args.Path}})
	}))
	defer backend.Close()
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"已收到所选记录\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer model.Close()
	seen := ""
	cfg := agent.Config{LLM: llm.New(model.URL, "", "fixture"), SystemFn: func(*agent.Session) string { return "system" },
		FileCtxFn: func(s *agent.Session) string {
			for _, m := range s.Manifests {
				seen += m.ManifestID + ","
			}
			return seen
		}}
	storage := t.TempDir()
	s := New(cfg, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	selected := uploadDraft(t, h, id, "selected.npy", true)
	removed := uploadDraft(t, h, id, "removed.npy", true)
	if len(s.sessions[id].Manifests) != 0 || len(s.sessions[id].StagedManifests) != 2 {
		t.Fatal("draft became active")
	}
	loaded, err := session.Load(storage, id, nil)
	if err != nil || len(loaded.Manifests) != 0 || len(loaded.StagedManifests) != 2 {
		t.Fatal("draft state not preserved", err)
	}
	if w := request(t, h, "GET", "/sessions/"+id+"/attachments/"+removed+"/file", ""); w.Code != 200 {
		t.Fatal("draft cannot be previewed", w.Code)
	}
	bad, _ := json.Marshal(map[string]any{"text": "分析", "attachments": []string{selected, "sig_not_uploaded"}})
	if w := request(t, h, "POST", "/sessions/"+id+"/messages", string(bad)); w.Code != 400 {
		t.Fatal(w.Code)
	}
	if len(s.sessions[id].Manifests) != 0 {
		t.Fatal("invalid message partially committed attachments")
	}
	valid, _ := json.Marshal(map[string]any{"text": "分析所选记录", "attachments": []string{selected}})
	if w := request(t, h, "POST", "/sessions/"+id+"/messages", string(valid)); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if seen != selected+"," || len(s.sessions[id].Manifests) != 1 || len(s.sessions[id].StagedManifests) != 1 || s.sessions[id].StagedManifests[0].ManifestID != removed {
		t.Fatal("unsubmitted file entered model context", seen)
	}
	loaded, err = session.Load(storage, id, nil)
	if err != nil || len(loaded.Manifests) != 1 || len(loaded.StagedManifests) != 1 {
		t.Fatal("commit not persisted", err)
	}
	committed := uploadDraft(t, h, id, "direct.npy", false)
	if s.sessions[id].Manifests[1].ManifestID != committed {
		t.Fatal("ordinary API uploads changed behavior")
	}
}
