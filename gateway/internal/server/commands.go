package server

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/skills"
)

func (s *Server) selectSkill(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name string `json:"name"`
	}
	if json.NewDecoder(r.Body).Decode(&in) != nil {
		writeJSON(w, 400, map[string]string{"error": "需要 skill 名称"})
		return
	}
	var selected *skills.Skill
	for _, skill := range s.Cfg.Skills {
		if skill.Name == in.Name {
			selected = skill
			break
		}
	}
	if in.Name != "" && selected == nil {
		writeJSON(w, 404, map[string]string{"error": "分析方法不存在"})
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	ses := s.getLocked(r.PathValue("id"))
	if ses == nil || ses.DeletedAt != nil {
		writeJSON(w, 404, map[string]string{"error": "会话不存在"})
		return
	}
	if s.cancels[ses.ID] != nil || s.uploading[ses.ID] {
		writeJSON(w, 409, map[string]string{"error": "请在本轮结束后切换分析方法"})
		return
	}
	previous, updated := ses.ActiveSkill, ses.UpdatedAt
	ses.ActiveSkill = selected
	ses.UpdatedAt = time.Now()
	if err := session.Save(s.StorageDir, ses); err != nil {
		ses.ActiveSkill, ses.UpdatedAt = previous, updated
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.publishLocked(ses)
	writeJSON(w, 200, map[string]string{"active_skill": skillName(ses)})
}

func (s *Server) compactSession(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	ses := s.getLocked(r.PathValue("id"))
	if ses == nil || ses.DeletedAt != nil {
		s.mu.Unlock()
		writeJSON(w, 404, map[string]string{"error": "会话不存在"})
		return
	}
	if s.cancels[ses.ID] != nil || s.uploading[ses.ID] {
		s.mu.Unlock()
		writeJSON(w, 409, map[string]string{"error": "请等待本轮完成后整理上下文"})
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	s.cancels[ses.ID] = cancel
	s.publishLocked(ses)
	s.mu.Unlock()
	err := agent.DefaultCompactor(s.Cfg.LLM, 1, 8, func(current *agent.Session) int { return agent.ContextTokens(s.Cfg, current) })(ctx, ses, func(agent.Event) {})
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.cancels, ses.ID)
	ses.UpdatedAt = time.Now()
	if err == nil {
		err = session.Save(s.StorageDir, ses)
	}
	s.publishLocked(ses)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"status": "ok", "archived_messages": len(ses.ArchivedMessages), "estimated_tokens": agent.ContextTokens(s.Cfg, ses)})
}
