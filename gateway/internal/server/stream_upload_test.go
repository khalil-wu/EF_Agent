package server

import (
	"bytes"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/worker"
)

func uploadBody(t *testing.T, fieldsFirst bool, fileName string, data []byte) (*bytes.Buffer, string) {
	t.Helper()
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	writeFields := func() {
		_ = form.WriteField("staged", "true")
		_ = form.WriteField("upload_group_id", "upl_0123456789abcdef")
	}
	if fieldsFirst {
		writeFields()
	}
	file, _ := form.CreateFormFile("file", fileName)
	_, _ = file.Write(data)
	if !fieldsFirst {
		writeFields()
	}
	_ = form.Close()
	return &body, form.FormDataContentType()
}

func uploadRequest(h http.Handler, id string, body *bytes.Buffer, contentType string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", "/sessions/"+id+"/attachments", body)
	r.Header.Set("Content-Type", contentType)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func echoUploadWorker(t *testing.T) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Args struct {
				Path string `json:"path"`
			} `json:"args"`
		}
		_ = json.NewDecoder(r.Body).Decode(&input)
		name := filepath.Base(input.Args.Path)
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "manifest": map[string]any{
			"manifest_id": "sig_" + strings.TrimSuffix(name, filepath.Ext(name)), "modality": "eeg",
			"attachment_sha256": name, "source_path": input.Args.Path}})
	}))
}

func incomingEntries(t *testing.T, storage, id string) []os.DirEntry {
	t.Helper()
	entries, err := os.ReadDir(filepath.Join(storage, "uploads", id, ".incoming"))
	if err != nil {
		return nil // directory already removed: also fine
	}
	return entries
}

func TestStreamedUploadWritesOnceAndCollectsFieldsInEitherOrder(t *testing.T) {
	backend := echoUploadWorker(t)
	defer backend.Close()
	cfg := agent.Config{LLM: llm.New("http://127.0.0.1:9", "", "fixture"), SystemFn: func(*agent.Session) string { return "system" }}
	storage := t.TempDir()
	s := New(cfg, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	data := []byte("streamed bytes fixture")
	for _, fieldsFirst := range []bool{true, false} {
		name := "rec-order-" + map[bool]string{true: "a", false: "b"}[fieldsFirst] + ".npy"
		body, contentType := uploadBody(t, fieldsFirst, name, data)
		w := uploadRequest(h, id, body, contentType)
		if w.Code != 200 {
			t.Fatalf("fieldsFirst=%v: %d %s", fieldsFirst, w.Code, w.Body.String())
		}
		placed := filepath.Join(storage, "uploads", id, "upl_0123456789abcdef", name)
		got, err := os.ReadFile(placed)
		if err != nil || !bytes.Equal(got, data) {
			t.Fatalf("fieldsFirst=%v: placed bytes %v %v", fieldsFirst, got, err)
		}
	}
	if entries := incomingEntries(t, storage, id); len(entries) > 0 {
		t.Fatalf(".incoming leftovers after success: %v", entries)
	}
}

func TestStreamedUploadSameNameDifferentContentRejectedAndIdenticalReused(t *testing.T) {
	backend := echoUploadWorker(t)
	defer backend.Close()
	cfg := agent.Config{LLM: llm.New("http://127.0.0.1:9", "", "fixture"), SystemFn: func(*agent.Session) string { return "system" }}
	storage := t.TempDir()
	s := New(cfg, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	group := filepath.Join(storage, "uploads", id, "upl_0123456789abcdef")

	body, contentType := uploadBody(t, true, "dup.npy", []byte("content-a"))
	if w := uploadRequest(h, id, body, contentType); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	body, contentType = uploadBody(t, true, "dup.npy", []byte("content-b"))
	w := uploadRequest(h, id, body, contentType)
	if w.Code != 400 || !strings.Contains(w.Body.String(), "同名不同内容") {
		t.Fatalf("same-name different content: %d %s", w.Code, w.Body.String())
	}
	if entries := incomingEntries(t, storage, id); len(entries) > 0 {
		t.Fatalf(".incoming leftovers after rejection: %v", entries)
	}
	body, contentType = uploadBody(t, true, "dup.npy", []byte("content-a"))
	w = uploadRequest(h, id, body, contentType)
	if w.Code != 200 {
		t.Fatalf("identical re-upload: %d %s", w.Code, w.Body.String())
	}
	files, err := filepath.Glob(filepath.Join(group, "*"))
	if err != nil || len(files) != 1 || filepath.Base(files[0]) != "dup.npy" {
		t.Fatalf("expected single reused file, got %v %v", files, err)
	}
	if entries := incomingEntries(t, storage, id); len(entries) > 0 {
		t.Fatalf(".incoming leftovers after reuse: %v", entries)
	}
}

func TestStreamedUploadOverLimitReturns413AndCleansUp(t *testing.T) {
	backend := echoUploadWorker(t)
	defer backend.Close()
	cfg := agent.Config{LLM: llm.New("http://127.0.0.1:9", "", "fixture"), SystemFn: func(*agent.Session) string { return "system" }}
	storage := t.TempDir()
	s := New(cfg, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	old := maxUploadBytes
	maxUploadBytes = 64
	defer func() { maxUploadBytes = old }()

	body, contentType := uploadBody(t, true, "big.npy", bytes.Repeat([]byte("x"), 4096))
	w := uploadRequest(h, id, body, contentType)
	if w.Code != 413 || !strings.Contains(w.Body.String(), "1 GB") {
		t.Fatalf("over limit: %d %s", w.Code, w.Body.String())
	}
	if entries := incomingEntries(t, storage, id); len(entries) > 0 {
		t.Fatalf(".incoming leftovers after 413: %v", entries)
	}
}
