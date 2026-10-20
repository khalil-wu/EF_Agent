package server

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/hooks"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/worker"
)

func TestRepeatedRecordGroupsKeepReferencedNamesAndNewSignal(t *testing.T) {
	reader := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input struct{ Args struct{ Path string } }
		json.NewDecoder(r.Body).Decode(&input)
		path := input.Args.Path
		header, err := os.ReadFile(path)
		if err != nil {
			t.Error(err)
			return
		}
		name := strings.TrimSpace(strings.TrimPrefix(string(header), "DataFile="))
		signal, err := os.ReadFile(filepath.Join(filepath.Dir(path), name))
		if err != nil {
			t.Error(err)
			return
		}
		sha := fmt.Sprintf("%x", sha256.Sum256(signal))
		json.NewEncoder(w).Encode(map[string]any{"status": "ok", "manifest": map[string]any{
			"manifest_id": "sig_" + sha[:8], "modality": "eeg", "attachment_sha256": sha,
			"source_path": path, "source_files": []string{path, filepath.Join(filepath.Dir(path), name)}}})
	}))
	defer reader.Close()
	s := New(agent.Config{}, worker.New(reader.URL), t.TempDir())
	h := s.Routes()
	id := create(t, h)
	paths := []string{}
	for _, signal := range []string{"OLD_SIGNAL", "NEW_SIGNAL"} {
		var body bytes.Buffer
		form := multipart.NewWriter(&body)
		for _, member := range []struct{ name, data string }{{"signal.eeg", signal}, {"record.vhdr", "DataFile=signal.eeg"}} {
			file, _ := form.CreateFormFile("file", member.name)
			file.Write([]byte(member.data))
		}
		form.Close()
		r := httptest.NewRequest("POST", "/sessions/"+id+"/attachments", &body)
		r.Header.Set("Content-Type", form.FormDataContentType())
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
		var result struct{ Manifest contracts.Manifest }
		json.Unmarshal(w.Body.Bytes(), &result)
		paths = append(paths, result.Manifest.SourcePath)
		data, err := os.ReadFile(filepath.Join(filepath.Dir(result.Manifest.SourcePath), "signal.eeg"))
		if err != nil || string(data) != signal {
			t.Fatal(string(data), err)
		}
		if result.Manifest.UploadGroupID == "" || filepath.Base(result.Manifest.SourcePath) != "record.vhdr" {
			t.Fatal(result.Manifest)
		}
	}
	if filepath.Dir(paths[0]) == filepath.Dir(paths[1]) || len(s.sessions[id].Manifests) != 2 {
		t.Fatal("record versions mixed")
	}
	old, _ := os.ReadFile(filepath.Join(filepath.Dir(paths[0]), "signal.eeg"))
	if string(old) != "OLD_SIGNAL" {
		t.Fatal("old record changed")
	}
}

func TestDiscardedDraftDoesNotReturnAfterSessionRestart(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"status": "ok", "manifest": map[string]any{
			"manifest_id": "doc_audit01", "modality": "document", "attachment_sha256": "draft"}})
	}))
	defer backend.Close()
	storage := t.TempDir()
	s := New(agent.Config{}, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	mid := uploadDraft(t, h, id, "note.md", true)
	if w := request(t, h, "DELETE", "/sessions/"+id+"/attachments/"+mid, ""); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	restored, err := session.Load(storage, id, nil)
	if err != nil || len(restored.StagedManifests) != 0 {
		t.Fatal("draft returned", err)
	}
}

func TestCompletedToolBatchIsRecoverableBeforeNextModelResponse(t *testing.T) {
	continued := make(chan struct{})
	modelCalls := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		modelCalls++
		w.Header().Set("Content-Type", "text/event-stream")
		if modelCalls == 1 {
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_ready\",\"function\":{\"name\":\"run_python\",\"arguments\":\"{\\\"code\\\":\\\"fixture\\\"}\"}}]}}]}\n\ndata: [DONE]\n\n")
			return
		}
		close(continued)
		w.WriteHeader(200)
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer model.Close()
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/tools/run_python/events" {
			t.Errorf("script did not use its real event transport: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		json.NewEncoder(w).Encode(map[string]any{"type": "result", "result": map[string]any{
			"status": "ok", "artifact": map[string]any{"artifact_id": "art_abc123def456", "kind": "table", "summary": map[string]int{"rows": 2}}}})
	}))
	defer backend.Close()
	all := map[string]contracts.ToolDefinition{"run_python": {Name: "run_python", PermissionModeMin: "readonly"}}
	storage := t.TempDir()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), Executor: worker.New(backend.URL), AllTools: all,
		SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }, Hooks: hooks.Build(all, storage, nil)}
	s := New(cfg, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	defer request(t, h, "POST", "/sessions/"+id+"/stop", "")
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- request(t, h, "POST", "/sessions/"+id+"/messages", `{"text":"run fixture"}`) }()
	select {
	case <-continued:
	case <-time.After(5 * time.Second):
		t.Fatal("next model request did not start")
	}
	restored, err := session.Load(storage, id, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !restored.LastStopped || len(restored.Artifacts) != 1 || restored.Messages[len(restored.Messages)-1].Role != "tool" {
		t.Fatal("completed batch was not recoverable", restored)
	}
	if !strings.Contains(restored.Messages[len(restored.Messages)-1].Content, "art_abc123def456") {
		t.Fatal("lost result identity")
	}
	request(t, h, "POST", "/sessions/"+id+"/stop", "")
	<-done
}

func TestFailedWorkerResultReachesPublicFailureAndErrorCount(t *testing.T) {
	modelCalls := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		modelCalls++
		w.Header().Set("Content-Type", "text/event-stream")
		if modelCalls == 1 {
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_failed\",\"function\":{\"name\":\"run_python\",\"arguments\":\"{}\"}}]}}]}\n\ndata: [DONE]\n\n")
		} else {
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"The script timed out.\"}}]}\n\ndata: [DONE]\n\n")
		}
	}))
	defer model.Close()
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/tools/run_python/events" {
			t.Errorf("script did not use its real event transport: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		json.NewEncoder(w).Encode(map[string]any{"type": "result", "result": map[string]any{
			"status": "error", "error": map[string]string{"code": "Timeout", "message": "fixture timed out"}}})
	}))
	defer backend.Close()
	all := map[string]contracts.ToolDefinition{"run_python": {Name: "run_python", PermissionModeMin: "readonly"}}
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), Executor: worker.New(backend.URL), AllTools: all,
		SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	s := New(cfg, worker.New(backend.URL), t.TempDir())
	h := s.Routes()
	id := create(t, h)
	w := request(t, h, "POST", "/sessions/"+id+"/messages", `{"text":"fixture"}`)
	if !strings.Contains(w.Body.String(), `"is_error":true`) || !strings.Contains(w.Body.String(), "fixture timed out") || s.sessions[id].ToolErrors != 1 || len(s.sessions[id].Artifacts) != 0 {
		t.Fatal("failed tool was reported as success", w.Body.String())
	}
}
