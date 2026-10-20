package server

import (
	"context"
	"encoding/json"
	"net/http"
	"slices"
	"strings"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/session"
)

func (s *Server) conversationModel(ses *agent.Session) string {
	if ses.LLMModel != "" {
		return ses.LLMModel
	}
	return s.ModelName
}

func (s *Server) conversationModels(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	models, err := s.Cfg.LLM.Models(ctx)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"models": models, "default_model": s.ModelName})
}

func (s *Server) selectConversationModel(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Model string `json:"model"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || strings.TrimSpace(input.Model) == "" {
		writeJSON(w, 400, map[string]string{"error": "请选择对话模型"})
		return
	}
	input.Model = strings.TrimSpace(input.Model)
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	models, err := s.Cfg.LLM.Models(ctx)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	if !slices.Contains(models, input.Model) {
		writeJSON(w, 400, map[string]string{"error": "该模型不在网关模型列表中"})
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
		writeJSON(w, 409, map[string]string{"error": "请在本轮完成后切换模型"})
		return
	}
	previous, updated := ses.LLMModel, ses.UpdatedAt
	ses.LLMModel, ses.UpdatedAt = input.Model, time.Now()
	if err := session.Save(s.StorageDir, ses); err != nil {
		ses.LLMModel, ses.UpdatedAt = previous, updated
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.publishLocked(ses)
	writeJSON(w, 200, map[string]string{"llm_model": ses.LLMModel})
}
