package server

import (
	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/skills"
	"encoding/json"
	"testing"
)

func TestManualSkillSelectionPersistsAndRejectsUnknown(t *testing.T) {
	dir := t.TempDir()
	catalog := []*skills.Skill{{Name: "spectral_analysis"}}
	server := New(agent.Config{Skills: catalog}, nil, dir)
	h := server.Routes()
	id := create(t, h)
	if w := request(t, h, "PATCH", "/sessions/"+id+"/skill", `{"name":"spectral_analysis"}`); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	loaded, err := session.Load(dir, id, catalog)
	if err != nil || loaded.ActiveSkill.Name != "spectral_analysis" {
		t.Fatalf("%v %v", loaded, err)
	}
	if w := request(t, h, "PATCH", "/sessions/"+id+"/skill", `{"name":"imaginary"}`); w.Code != 404 {
		t.Fatal(w.Body.String())
	}
	if w := request(t, h, "PATCH", "/sessions/"+id+"/skill", `{"name":""}`); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if server.get(id).ActiveSkill != nil {
		t.Fatal("auto mode did not clear active skill")
	}
}

func TestArchivedMessagesRemainVisibleButOutsideModelContext(t *testing.T) {
	dir := t.TempDir()
	ses := &agent.Session{ID: "ses_aabbcc112233", ArchivedMessages: []llm.Message{{Role: "user", Content: "earlier"}}, Messages: []llm.Message{{Role: "user", Content: "current"}}}
	if err := session.Save(dir, ses); err != nil {
		t.Fatal(err)
	}
	h := New(agent.Config{}, nil, dir).Routes()
	w := request(t, h, "GET", "/sessions/"+ses.ID, "")
	var view struct {
		Messages []llm.Message `json:"messages"`
		Context  struct {
			Archived int `json:"archived_messages"`
			Active   int `json:"active_messages"`
		} `json:"context"`
	}
	json.Unmarshal(w.Body.Bytes(), &view)
	if len(view.Messages) != 2 || view.Messages[0].Content != "earlier" || view.Context.Active != 1 || view.Context.Archived != 1 {
		t.Fatal(w.Body.String())
	}
}
