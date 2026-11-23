package server

import (
	"context"
	"encoding/json"
	"net/http"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
)

func (s *Server) modelCapability(model string) llm.ModelCapability {
	return llm.CapabilityFor(model, s.ModelCapabilities)
}

func (s *Server) conversationModel(ses *agent.Session) string {
	if ses.LLMModel != "" {
		return ses.LLMModel
	}
	return s.ModelName
}

func (s *Server) effectiveVisionModel(ses *agent.Session) string {
	if ses.VisionModel != "" {
		return ses.VisionModel
	}
	if s.VisionModelName != "" {
		return s.VisionModelName
	}
	return s.conversationModel(ses)
}

func (s *Server) defaultVisionModel(models []string) string {
	if s.VisionModelName != "" && (len(models) == 0 || slices.Contains(models, s.VisionModelName)) {
		return s.VisionModelName
	}
	for _, m := range models {
		if s.modelCapability(m).ImageInput {
			return m
		}
	}
	if imgs := s.imageModels(); len(imgs) > 0 {
		return imgs[0]
	}
	return ""
}

func (s *Server) conversationModels(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	models, err := s.Cfg.LLM.Models(ctx)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	capabilities := map[string]llm.ModelCapability{}
	for _, model := range models {
		capabilities[model] = s.modelCapability(model)
	}
	writeJSON(w, 200, map[string]any{
		"models":               models,
		"default_model":        s.ModelName,
		"default_vision_model": s.defaultVisionModel(models),
		"capabilities":         capabilities,
	})
}

func isImageFilename(name string) bool {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff":
		return true
	default:
		return false
	}
}

func (s *Server) hasImageInput(ses *agent.Session, selected []string) bool {
	return s.hasImageInputWithRefs(ses, selected, nil)
}

func (s *Server) hasImageInputWithRefs(ses *agent.Session, selected []string, refs []llm.InputReference) bool {
	for _, manifest := range ses.Manifests {
		if manifest.Modality == "image" {
			return true
		}
	}
	for _, manifest := range ses.StagedManifests {
		if manifest.Modality == "image" && slices.Contains(selected, manifest.ManifestID) {
			return true
		}
	}
	for _, ref := range refs {
		if ref.Kind != "directory" && (isImageFilename(ref.Path) || isImageFilename(ref.Name)) {
			return true
		}
	}
	return false
}

func (s *Server) imageModels() []string {
	models := []string{}
	for model, capability := range s.ModelCapabilities {
		if capability.ImageInput {
			models = append(models, model)
		}
	}
	sort.Strings(models)
	return models
}

func (s *Server) selectConversationModel(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Model       *string `json:"model"`
		VisionModel *string `json:"vision_model"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		writeJSON(w, 400, map[string]string{"error": "请选择对话模型"})
		return
	}
	var chatModel, visionModel string
	hasChat := input.Model != nil && strings.TrimSpace(*input.Model) != ""
	hasVision := input.VisionModel != nil
	if hasChat {
		chatModel = strings.TrimSpace(*input.Model)
	}
	if hasVision {
		visionModel = strings.TrimSpace(*input.VisionModel)
	}
	if !hasChat && !hasVision {
		writeJSON(w, 400, map[string]string{"error": "请选择对话模型"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	models, err := s.Cfg.LLM.Models(ctx)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	if hasChat && !slices.Contains(models, chatModel) {
		writeJSON(w, 400, map[string]string{"error": "该模型不在网关模型列表中"})
		return
	}
	if hasVision && visionModel != "" && visionModel != "auto" && !slices.Contains(models, visionModel) {
		writeJSON(w, 400, map[string]string{"error": "该识图模型不在网关模型列表中"})
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
	previousLLM, previousVision, updated := ses.LLMModel, ses.VisionModel, ses.UpdatedAt
	nextLLM := ses.LLMModel
	if hasChat {
		nextLLM = chatModel
	}
	nextVision := ses.VisionModel
	if hasVision {
		if visionModel == "" || visionModel == "auto" {
			nextVision = ""
		} else {
			if !s.modelCapability(visionModel).ImageInput {
				writeJSON(w, 400, map[string]any{"error": "所选识图模型未验证支持图片输入。", "code": "image_model_required", "image_models": s.imageModels()})
				return
			}
			nextVision = visionModel
		}
	}
	effVision := nextVision
	if effVision == "" {
		if s.VisionModelName != "" {
			effVision = s.VisionModelName
		} else if nextLLM != "" {
			effVision = nextLLM
		} else {
			effVision = s.ModelName
		}
	}
	if s.hasImageInput(ses, nil) && !s.modelCapability(effVision).ImageInput {
		writeJSON(w, 400, map[string]any{"error": "本会话包含图片，请选择已验证的看图模型，或单独指定识图模型。", "code": "image_model_required", "image_models": s.imageModels()})
		return
	}
	ses.LLMModel, ses.VisionModel, ses.UpdatedAt = nextLLM, nextVision, time.Now()
	if err := session.Save(s.StorageDir, ses); err != nil {
		ses.LLMModel, ses.VisionModel, ses.UpdatedAt = previousLLM, previousVision, updated
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.publishLocked(ses)
	writeJSON(w, 200, map[string]string{
		"llm_model":              s.conversationModel(ses),
		"vision_model":           ses.VisionModel,
		"effective_vision_model": s.effectiveVisionModel(ses),
	})
}
