package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/worker"
)

func TestStagedTextPreviewDoesNotSubmitTheDocument(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/manifests/doc_preview/preview" {
			fmt.Fprint(w, `{"content":"explicit transport fixture · 采集说明","content_truncated":false}`)
			return
		}
		var input struct{ Args struct{ Path string } }
		json.NewDecoder(r.Body).Decode(&input)
		json.NewEncoder(w).Encode(map[string]any{"status": "ok", "manifest": map[string]any{"manifest_id": "doc_preview", "modality": "document", "source_path": input.Args.Path, "source_format": "md", "attachment_sha256": "doc_sha"}})
	}))
	defer backend.Close()
	s := New(agent.Config{}, worker.New(backend.URL), t.TempDir())
	h := s.Routes()
	id := create(t, h)
	mid := uploadDraft(t, h, id, "methods.md", true)
	w := request(t, h, "GET", "/sessions/"+id+"/attachments/"+mid+"/preview", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "explicit transport fixture") {
		t.Fatal(w.Code, w.Body.String())
	}
	if len(s.sessions[id].Manifests) != 0 || len(s.sessions[id].StagedManifests) != 1 || len(s.sessions[id].Messages) != 0 {
		t.Fatal("preview changed submission state")
	}
	w = request(t, h, "GET", "/sessions/"+id+"/attachments/"+mid+"/file?download=1", "")
	if w.Code != 200 || !strings.Contains(w.Header().Get("Content-Disposition"), "methods.md") {
		t.Fatal(w.Code, w.Header())
	}
	other := create(t, h)
	if w = request(t, h, "GET", "/sessions/"+other+"/attachments/"+mid+"/preview", ""); w.Code != 404 {
		t.Fatal("cross-session preview allowed", w.Code)
	}
}

func TestHistoricalAttachmentUsesCurrentMetadataAndStillServesItsVideoRange(t *testing.T) {
	storage := t.TempDir()
	directory := filepath.Join(storage, "uploads", "original")
	os.MkdirAll(directory, 0755)
	path := filepath.Join(directory, "participant.mp4")
	os.WriteFile(path, []byte("0123456789"), 0644)
	old := &contracts.Manifest{ManifestID: "vid_original", Modality: "video", AttachmentSHA256: "same_video", SourcePath: path, SourceFormat: "mp4", OriginalName: "participant.mp4"}
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/manifests/vid_original" {
			t.Error(r.URL.Path)
		}
		json.NewEncoder(w).Encode(old)
	}))
	defer backend.Close()
	s := New(agent.Config{}, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	current := *old
	current.ManifestID = "vid_updated"
	current.Sync = json.RawMessage(`{"start_offset_s":3}`)
	ses := s.sessions[id]
	ses.Manifests = []*contracts.Manifest{&current}
	ses.Messages = []llm.Message{{Role: "user", Presentation: &llm.InputPresentation{Attachments: []llm.InputAttachment{{ID: old.ManifestID, Name: old.OriginalName, Modality: "video"}}}}}
	s.mu.Lock()
	s.publishLocked(ses)
	s.mu.Unlock()
	w := request(t, h, "GET", "/sessions/"+id+"/attachments/vid_original/preview", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "vid_updated") || !strings.Contains(w.Body.String(), "start_offset_s") {
		t.Fatal(w.Code, w.Body.String())
	}
	r := httptest.NewRequest("GET", "/sessions/"+id+"/attachments/vid_original/file", nil)
	r.Header.Set("Range", "bytes=2-5")
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 206 || w.Body.String() != "2345" {
		t.Fatal(w.Code, w.Body.String())
	}
}

func TestEEGPreviewUsesItsActualSourceAndKeepsAssessmentLabelsPrivate(t *testing.T) {
	storage := t.TempDir()
	dir := filepath.Join(storage, "uploads", "signal")
	os.MkdirAll(dir, 0755)
	path := filepath.Join(dir, "signal.npy")
	os.WriteFile(path, []byte("signal"), 0644)
	called := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called++
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path != "/tools/plot" || !strings.Contains(string(body), `"source_id":"sig_current"`) {
			t.Error(r.URL.Path, string(body))
		}
		fmt.Fprint(w, `{"status":"ok","artifact":{"artifact_id":"art_realwave","kind":"plot"}}`)
	}))
	defer backend.Close()
	s := New(agent.Config{}, worker.New(backend.URL), storage)
	h := s.Routes()
	id := create(t, h)
	rate := 128.0
	ses := s.sessions[id]
	ses.Manifests = []*contracts.Manifest{{ManifestID: "sig_current", Modality: "eeg", SourcePath: path, SampleRateHz: &rate, Labels: json.RawMessage(`{"private_truth":42}`)}}
	s.mu.Lock()
	s.publishLocked(ses)
	s.mu.Unlock()
	w := request(t, h, "GET", "/sessions/"+id+"/attachments/sig_current/preview", "")
	if w.Code != 200 || called != 1 || !strings.Contains(w.Body.String(), "art_realwave") || strings.Contains(w.Body.String(), "private_truth") {
		t.Fatal(w.Code, w.Body.String())
	}
	ses.Previews = map[string]json.RawMessage{"sig_current": json.RawMessage(`{"artifact_id":"art_savedwave"}`)}
	s.mu.Lock()
	s.publishLocked(ses)
	s.mu.Unlock()
	w = request(t, h, "GET", "/sessions/"+id+"/attachments/sig_current/preview", "")
	if w.Code != 200 || called != 1 || !strings.Contains(w.Body.String(), "art_savedwave") {
		t.Fatal("stored waveform was not reused", w.Body.String())
	}
}

func TestRegisteredWorkspaceFilesPreviewAndServeFromTheirOriginalDirectory(t *testing.T) {
	storage, folder := t.TempDir(), t.TempDir()
	os.MkdirAll(filepath.Join(storage, "workspaces"), 0755)
	record, _ := json.Marshal(map[string]string{"workspace_id": "ws_abcdef123456", "root": folder})
	os.WriteFile(filepath.Join(storage, "workspaces", "ws_abcdef123456.json"), record, 0644)
	path := filepath.Join(folder, "record.npz")
	os.WriteFile(path, []byte("0123456789"), 0644)
	s := New(agent.Config{}, nil, storage)
	h := s.Routes()
	id := create(t, h)
	ses := s.sessions[id]
	ses.WorkspaceID = "ws_abcdef123456"
	ses.Manifests = []*contracts.Manifest{{ManifestID: "sig_workspace", Modality: "eeg", SourcePath: path, SourceFormat: "npz", OriginalName: "record.npz"}}
	ses.Previews = map[string]json.RawMessage{"sig_workspace": json.RawMessage(`{"artifact_id":"art_workspace_wave","kind":"plot"}`)}
	s.mu.Lock()
	s.publishLocked(ses)
	s.mu.Unlock()
	w := request(t, h, "GET", "/sessions/"+id+"/attachments/sig_workspace/preview", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "art_workspace_wave") {
		t.Fatal(w.Code, w.Body.String())
	}
	r := httptest.NewRequest("GET", "/sessions/"+id+"/attachments/sig_workspace/file", nil)
	r.Header.Set("Range", "bytes=2-5")
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 206 || w.Body.String() != "2345" {
		t.Fatal(w.Code, w.Body.String())
	}
	ses.Manifests[0].SourcePath = filepath.Join(t.TempDir(), "unrelated.npz")
	s.mu.Lock()
	s.publishLocked(ses)
	s.mu.Unlock()
	w = request(t, h, "GET", "/sessions/"+id+"/attachments/sig_workspace/preview", "")
	if w.Code != 404 {
		t.Fatal("unrelated file treated as workspace input", w.Code)
	}
}
