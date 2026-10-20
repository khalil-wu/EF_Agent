package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/worker"
)

func TestRemoveWorkspaceDetachesLiveColdAndDeletedSessions(t *testing.T) {
	identifier := "ws_abcdef123456"
	workerCalls := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		workerCalls++
		if r.Method != "DELETE" || r.URL.Path != "/workspaces/"+identifier {
			t.Errorf("unexpected worker request: %s %s", r.Method, r.URL.Path)
		}
		fmt.Fprintf(w, `{"workspace_id":%q,"removed":true}`, identifier)
	}))
	defer backend.Close()
	dir := t.TempDir()
	s := New(agent.Config{}, worker.New(backend.URL), dir)
	h := s.Routes()
	ids := []string{create(t, h), create(t, h), create(t, h), create(t, h)}
	for i, id := range ids {
		ses := s.get(id)
		ses.WorkspaceID = identifier
		ses.Title = fmt.Sprintf("keep-conversation-%d", i)
		if i == 2 {
			now := time.Now()
			ses.DeletedAt = &now
		}
		if i == 3 {
			ses.WorkspaceID = "ws_111111111111"
		}
		if err := session.Save(dir, ses); err != nil {
			t.Fatal(err)
		}
	}
	delete(s.sessions, ids[1])
	delete(s.sessions, ids[2])
	w := request(t, h, "DELETE", "/workspaces/"+identifier, "")
	if w.Code != 200 || workerCalls != 1 {
		t.Fatal(w.Code, w.Body.String(), workerCalls)
	}
	var result struct {
		Detached int `json:"detached_sessions"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil || result.Detached != 3 {
		t.Fatal(w.Body.String(), err)
	}
	for i, id := range ids {
		ses, err := session.Load(dir, id, nil)
		if err != nil || ses.Title != fmt.Sprintf("keep-conversation-%d", i) {
			t.Fatal("conversation changed", id, err)
		}
		if i < 3 && (ses.WorkspaceID != "" || s.sessions[id].WorkspaceID != "") {
			t.Fatal("removed workspace retained as session context", id)
		}
		if i == 2 && ses.DeletedAt == nil {
			t.Fatal("removal restored a deleted conversation")
		}
		if i == 3 && ses.WorkspaceID != "ws_111111111111" {
			t.Fatal("unrelated workspace changed")
		}
	}
}

func TestRemoveWorkspaceWaitsForAnalysisOrUpload(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("busy workspace reached worker") }))
	defer backend.Close()
	for _, operation := range []string{"analysis", "upload"} {
		t.Run(operation, func(t *testing.T) {
			s := New(agent.Config{}, worker.New(backend.URL), t.TempDir())
			h := s.Routes()
			id := create(t, h)
			s.get(id).WorkspaceID = "ws_abcdef123456"
			if operation == "analysis" {
				s.cancels[id] = func() {}
			} else {
				s.uploading[id] = true
			}
			if result := request(t, h, "DELETE", "/workspaces/ws_abcdef123456", ""); result.Code != 409 {
				t.Fatal(result.Code, result.Body.String())
			}
		})
	}
}

func TestRemoveWorkspaceWorkerFailureKeepsSessionBinding(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, `{"error":"disk unavailable"}`, 500) }))
	defer backend.Close()
	s := New(agent.Config{}, worker.New(backend.URL), t.TempDir())
	h := s.Routes()
	id := create(t, h)
	ses := s.get(id)
	ses.WorkspaceID = "ws_abcdef123456"
	if err := session.Save(s.StorageDir, ses); err != nil {
		t.Fatal(err)
	}
	if result := request(t, h, "DELETE", "/workspaces/ws_abcdef123456", ""); result.Code != 500 {
		t.Fatal(result.Code)
	}
	if s.get(id).WorkspaceID != "ws_abcdef123456" {
		t.Fatal("failed removal detached the session")
	}
}
