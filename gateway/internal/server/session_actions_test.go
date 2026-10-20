package server

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
)

func TestRenameDeleteRestorePreserveSessionData(t *testing.T) {
	dir := t.TempDir()
	s := New(agent.Config{}, nil, dir)
	h := s.Routes()
	id := create(t, h)
	for _, title := range []string{"", strings.Repeat("脑", 121)} {
		body, _ := json.Marshal(map[string]string{"title": title})
		if w := request(t, h, "PATCH", "/sessions/"+id, string(body)); w.Code != 400 {
			t.Fatal(w.Body.String())
		}
	}
	if w := request(t, h, "PATCH", "/sessions/"+id, `{"title":"静息态 · alpha 分析"}`); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if w := request(t, h, "DELETE", "/sessions/"+id, ""); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	restarted := New(agent.Config{}, nil, dir).Routes()
	if w := request(t, restarted, "GET", "/sessions", ""); strings.Contains(w.Body.String(), id) {
		t.Fatal("deleted session still listed")
	}
	if w := request(t, restarted, "GET", "/sessions?view=trash", ""); !strings.Contains(w.Body.String(), id) {
		t.Fatal("deleted session missing from trash")
	}
	if w := request(t, restarted, "GET", "/sessions/"+id, ""); w.Code != 404 {
		t.Fatal(w.Code)
	}
	if w := request(t, restarted, "POST", "/sessions/"+id+"/messages", `{"text":"hi"}`); w.Code != 404 {
		t.Fatal("deleted session accepted a message")
	}
	if w := request(t, restarted, "POST", "/sessions/"+id+"/restore", ""); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if w := request(t, restarted, "GET", "/sessions/"+id, ""); w.Code != 200 || !strings.Contains(w.Body.String(), "静息态 · alpha 分析") {
		t.Fatal(w.Body.String())
	}
	if w := request(t, restarted, "POST", "/sessions/"+id+"/restore", ""); w.Code != 409 {
		t.Fatal("restored session restored twice")
	}
}

func TestSessionManagementRejectsBusyWriter(t *testing.T) {
	s := New(agent.Config{}, nil, t.TempDir())
	h := s.Routes()
	id := create(t, h)
	_, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.cancels[id] = cancel
	for _, req := range [][3]string{{"DELETE", "", ""}, {"PATCH", "", `{"title":"changed"}`}, {"POST", "/clone", ""}} {
		if w := request(t, h, req[0], "/sessions/"+id+req[1], req[2]); w.Code != 409 {
			t.Fatalf("%v: %s", req, w.Body.String())
		}
	}
	delete(s.cancels, id)
	s.uploading[id] = true
	if w := request(t, h, "DELETE", "/sessions/"+id, ""); w.Code != 409 {
		t.Fatal("uploading session deleted")
	}
}

func TestCloneKeepsEvidenceAndCopiesScratchIndependently(t *testing.T) {
	dir := t.TempDir()
	id := "ses_aabbccddeeff"
	now := time.Now()
	source := &agent.Session{ID: id, Title: "频谱分析", CreatedAt: now, UpdatedAt: now, Mode: contracts.ModeReadonly,
		Messages:     []llm.Message{{Role: "user", Content: "绘制波形"}, {Role: "assistant", Content: "图已生成 artifact://art_aabbcc"}},
		Manifests:    []*contracts.Manifest{{ManifestID: "sig_abc", OriginalName: "sample.csv", NChannels: 2}},
		Artifacts:    []json.RawMessage{json.RawMessage(`{"artifact_id":"art_aabbcc","kind":"plot"}`)},
		ScratchFiles: []string{"notes.txt"}, Previews: map[string]json.RawMessage{"sig_abc": json.RawMessage(`{"artifact_id":"art_aabbcc"}`)},
	}
	if err := session.Save(dir, source); err != nil {
		t.Fatal(err)
	}
	scratch := filepath.Join(dir, "scratch", id)
	os.MkdirAll(scratch, 0o755)
	os.WriteFile(filepath.Join(scratch, "notes.txt"), []byte("original"), 0o644)
	s := New(agent.Config{}, nil, dir)
	h := s.Routes()
	w := request(t, h, "POST", "/sessions/"+id+"/clone", "")
	if w.Code != 201 {
		t.Fatal(w.Body.String())
	}
	var out struct {
		ID string `json:"session_id"`
	}
	json.Unmarshal(w.Body.Bytes(), &out)
	child, err := session.Load(dir, out.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	if child.ParentSessionID != id || len(child.Messages) != 2 || len(child.Manifests) != 1 || len(child.Artifacts) != 1 || len(child.Previews) != 1 {
		t.Fatalf("incomplete clone: %+v", child)
	}
	copied := filepath.Join(dir, "scratch", child.ID, "notes.txt")
	data, err := os.ReadFile(copied)
	if err != nil || string(data) != "original" {
		t.Fatalf("%s %v", data, err)
	}
	os.WriteFile(copied, []byte("branch"), 0o644)
	data, _ = os.ReadFile(filepath.Join(scratch, "notes.txt"))
	if string(data) != "original" {
		t.Fatal("branch modified source scratch")
	}
	child.Manifests[0].OriginalName = "changed.csv"
	if s.get(id).Manifests[0].OriginalName != "sample.csv" {
		t.Fatal("shared manifest object")
	}
	request(t, h, "DELETE", "/sessions/"+id, "")
	if w := request(t, h, "GET", "/sessions/"+child.ID, ""); w.Code != 200 {
		t.Fatal("source deletion broke branch")
	}
	if _, err := os.Stat(filepath.Join(scratch, "notes.txt")); err != nil {
		t.Fatal("deletion removed referenced data")
	}
	for _, format := range []string{"md", "json"} {
		w = request(t, h, "GET", "/sessions/"+child.ID+"/export?format="+format, "")
		if w.Code != 200 || !strings.Contains(w.Header().Get("Content-Disposition"), "attachment") || !strings.Contains(w.Body.String(), "art_aabbcc") {
			t.Fatal(w.Body.String())
		}
		if format == "json" && !json.Valid(w.Body.Bytes()) {
			t.Fatal("invalid exported JSON")
		}
	}
}
