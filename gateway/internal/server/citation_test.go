package server

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/session"
)

func TestSessionCitationsReadPublishedEvidenceWithoutCurrentIndexFallback(t *testing.T) {
	s := New(agent.Config{}, nil, t.TempDir())
	h := s.Routes()
	id := create(t, h)
	ses := s.get(id)
	ses.RememberSources([]json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef","text":"cited original","page":2}`)})
	if err := session.Save(s.StorageDir, ses); err != nil {
		t.Fatal(err)
	}
	s.mu.Lock()
	s.publishLocked(ses)
	s.mu.Unlock()
	ses.Sources["k_abcdef"] = json.RawMessage(`{"chunk_id":"k_abcdef","text":"unpublished newer text"}`)
	ses.Sources["k_123456"] = json.RawMessage(`{"chunk_id":"k_123456","text":"not published"}`)
	path := "/sessions/" + id + "/knowledge/"
	for _, handler := range []struct {
		name string
		s    *Server
	}{{"current", s}, {"restarted", New(agent.Config{}, nil, s.StorageDir)}} {
		t.Run(handler.name, func(t *testing.T) {
			w := request(t, handler.s.Routes(), "GET", path+"k_abcdef", "")
			if w.Code != 200 || !strings.Contains(w.Body.String(), "cited original") || strings.Contains(w.Body.String(), "newer text") {
				t.Fatalf("citation changed with the live index: %d %s", w.Code, w.Body.String())
			}
			missing := request(t, handler.s.Routes(), "GET", path+"k_123456", "")
			if missing.Code != 404 {
				t.Fatalf("unpublished citation reached the worker: %d %s", missing.Code, missing.Body.String())
			}
		})
	}
}

func TestAttachmentAndCitationReadersUseViewsWhileAgentStateChanges(t *testing.T) {
	s := New(agent.Config{}, nil, t.TempDir())
	h := s.Routes()
	id := create(t, h)
	ses := s.get(id)
	dir := filepath.Join(s.StorageDir, "uploads", id)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, "paper.txt")
	if err := os.WriteFile(file, []byte("published document"), 0o644); err != nil {
		t.Fatal(err)
	}
	ses.Manifests = []*contracts.Manifest{{ManifestID: "sig_abcdef", Modality: "document", SourcePath: file}}
	ses.RememberSources([]json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef","text":"published excerpt"}`)})
	s.mu.Lock()
	s.publishLocked(ses)
	s.mu.Unlock()
	stop, stopped := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(stopped)
		for {
			select {
			case <-stop:
				return
			default:
				ses.Manifests[0].SourcePath = filepath.Join(dir, "not-published.txt")
				ses.Sources["k_abcdef"] = json.RawMessage(`{"chunk_id":"k_abcdef","text":"live mutation"}`)
			}
		}
	}()
	defer func() { close(stop); <-stopped }()
	for i := 0; i < 20; i++ {
		w := request(t, h, "GET", "/sessions/"+id+"/attachments/sig_abcdef/file", "")
		if w.Code != 200 || w.Body.String() != "published document" {
			t.Fatalf("attachment reader touched live state: %d %s", w.Code, w.Body.String())
		}
		w = request(t, h, "GET", "/sessions/"+id+"/knowledge/k_abcdef", "")
		if w.Code != 200 || !strings.Contains(w.Body.String(), "published excerpt") {
			t.Fatalf("citation reader touched live state: %d %s", w.Code, w.Body.String())
		}
	}
}

func TestKnowledgeDocumentServesVersionedOriginal(t *testing.T) {
	s := New(agent.Config{}, nil, t.TempDir())
	digest := strings.Repeat("a", 64)
	dir := filepath.Join(s.StorageDir, "knowledge_sources")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, digest+".md"), []byte("# Cited source\n\nOriginal full context."), 0o644); err != nil {
		t.Fatal(err)
	}
	w := request(t, s.Routes(), "GET", "/knowledge/documents/"+digest+"/file", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "Original full context") || !strings.HasPrefix(w.Header().Get("Content-Type"), "text/plain") {
		t.Fatalf("source snapshot unavailable: %d %s", w.Code, w.Body.String())
	}
}

func TestKnowledgeDocumentServesRawPDFAndDownloadsHTML(t *testing.T) {
	s := New(agent.Config{}, nil, t.TempDir())
	digest := strings.Repeat("b", 64)
	dir := filepath.Join(s.StorageDir, "knowledge_sources")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, sample := range []struct{ format, body, media string }{
		{"pdf", "%PDF-1.7\noriginal bytes", "application/pdf"},
		{"html", "<html>archived article</html>", "text/html"},
	} {
		if err := os.WriteFile(filepath.Join(dir, digest+"."+sample.format), []byte(sample.body), 0o644); err != nil {
			t.Fatal(err)
		}
		w := request(t, s.Routes(), "GET", "/knowledge/documents/"+digest+"/file?format="+sample.format, "")
		if w.Code != 200 || w.Body.String() != sample.body || !strings.HasPrefix(w.Header().Get("Content-Type"), sample.media) {
			t.Fatalf("original %s unavailable: %d %s", sample.format, w.Code, w.Body.String())
		}
		if sample.format == "html" && !strings.HasPrefix(w.Header().Get("Content-Disposition"), "attachment") {
			t.Fatal("archived HTML must be downloaded, not executed on the application origin")
		}
	}
}
