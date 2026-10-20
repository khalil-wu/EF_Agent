package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
)

type attachmentView struct {
	Manifest *contracts.Manifest
	Preview  json.RawMessage
}

// Resolve the submitted/staged identity or an immutable attachment in this conversation's history.
// Metadata revisions may replace the active manifest without replacing the original uploaded file.
func (s *Server) resolveAttachment(r *http.Request) (*attachmentView, error) {
	id, manifestID := r.PathValue("id"), r.PathValue("manifest_id")
	if s.get(id) == nil {
		return nil, fmt.Errorf("会话不存在")
	}
	s.mu.Lock()
	view := s.views[id]
	s.mu.Unlock()
	var snapshot struct {
		WorkspaceID string                     `json:"workspace_id"`
		Manifests   []*contracts.Manifest      `json:"manifests"`
		Staged      []*contracts.Manifest      `json:"staged_manifests"`
		Messages    []llm.Message              `json:"messages"`
		Previews    map[string]json.RawMessage `json:"previews"`
	}
	if err := json.Unmarshal(view, &snapshot); err != nil {
		return nil, err
	}
	all := append(snapshot.Manifests, snapshot.Staged...)
	var manifest *contracts.Manifest
	for _, m := range all {
		if m.ManifestID == manifestID {
			manifest = m
			break
		}
	}
	if manifest == nil {
		owned := false
		for _, message := range snapshot.Messages {
			if message.Presentation == nil {
				continue
			}
			for _, file := range message.Presentation.Attachments {
				owned = owned || file.ID == manifestID
			}
			for _, file := range message.Presentation.References {
				owned = owned || file.ManifestID == manifestID
			}
		}
		if !owned {
			return nil, fmt.Errorf("附件不属于本会话")
		}
		body, code, err := s.Worker.Get(r.Context(), "/manifests/"+manifestID)
		if err != nil {
			return nil, err
		}
		if code != 200 {
			return nil, fmt.Errorf("附件记录无法读取")
		}
		if err := json.Unmarshal(body, &manifest); err != nil {
			return nil, err
		}
		for _, current := range all {
			if current.SameRecord(manifest) {
				manifest = current
				break
			}
		}
	}
	base, _ := filepath.Abs(filepath.Join(s.StorageDir, "uploads"))
	path, _ := filepath.Abs(manifest.SourcePath)
	rel, err := filepath.Rel(base, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		if snapshot.WorkspaceID == "" {
			return nil, fmt.Errorf("附件不在上传目录中")
		}
		body, err := os.ReadFile(filepath.Join(s.StorageDir, "workspaces", snapshot.WorkspaceID+".json"))
		if err != nil {
			return nil, err
		}
		var workspace struct {
			Root string `json:"root"`
		}
		if err := json.Unmarshal(body, &workspace); err != nil {
			return nil, err
		}
		rel, err = filepath.Rel(workspace.Root, path)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return nil, fmt.Errorf("文件不在当前工作区中")
		}
	}
	return &attachmentView{Manifest: manifest, Preview: snapshot.Previews[manifest.ManifestID]}, nil
}

func (s *Server) attachmentPreview(w http.ResponseWriter, r *http.Request) {
	attachment, err := s.resolveAttachment(r)
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	m := *attachment.Manifest
	m.Labels = nil // Preview does not expose assessment truth to an ongoing blind analysis.
	result := map[string]any{"manifest": &m, "file_url": fmt.Sprintf("/sessions/%s/attachments/%s/file", r.PathValue("id"), r.PathValue("manifest_id"))}
	if m.Modality == "image" {
		result["file_url"] = result["file_url"].(string) + "?view=1"
	}
	if m.Modality == "eeg" {
		if len(attachment.Preview) > 2 {
			result["preview"] = attachment.Preview
		} else if m.SampleRateHz != nil {
			ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
			defer cancel()
			args, _ := json.Marshal(map[string]any{"kind": "raw", "source_id": m.ManifestID})
			body, err := s.Worker.Call(ctx, "plot", args)
			if err != nil {
				writeJSON(w, 502, map[string]string{"error": "波形预览无法生成：" + err.Error()})
				return
			}
			var plot struct {
				Artifact json.RawMessage `json:"artifact"`
			}
			if err := json.Unmarshal(body, &plot); err != nil {
				writeJSON(w, 502, map[string]string{"error": err.Error()})
				return
			}
			result["preview"] = plot.Artifact
			if len(plot.Artifact) > 2 {
				// Persist the generated waveform so later previews and page reloads reuse it.
				s.mu.Lock()
				if ses := s.getLocked(r.PathValue("id")); ses != nil {
					if ses.Previews == nil {
						ses.Previews = map[string]json.RawMessage{}
					}
					ses.Previews[m.ManifestID] = plot.Artifact
					ses.UpdatedAt = time.Now()
					s.persist(ses)
				}
				s.mu.Unlock()
			}
		} else {
			result["preview_note"] = "记录结构已读入；提供采样率后可查看按秒显示的波形。"
		}
	}
	if m.Modality == "document" && strings.ToLower(m.SourceFormat) != "pdf" {
		body, code, err := s.Worker.Get(r.Context(), "/manifests/"+m.ManifestID+"/preview")
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
			return
		}
		if code != 200 {
			writeJSON(w, code, json.RawMessage(body))
			return
		}
		var preview map[string]json.RawMessage
		if err := json.Unmarshal(body, &preview); err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
			return
		}
		result["content"], result["content_truncated"] = preview["content"], preview["content_truncated"]
	}
	writeJSON(w, 200, result)
}

// Removing a draft updates the same submitted/staged sets used by rendering and retrieval.
func (s *Server) discardAttachment(w http.ResponseWriter, r *http.Request) {
	ses := s.get(r.PathValue("id"))
	if ses == nil {
		http.NotFound(w, r)
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cancels[ses.ID] != nil {
		writeJSON(w, 409, map[string]string{"error": "请等待当前分析完成后移除附件"})
		return
	}
	id := r.PathValue("manifest_id")
	remaining := make([]*contracts.Manifest, 0, len(ses.StagedManifests))
	for _, manifest := range ses.StagedManifests {
		if manifest.ManifestID != id {
			remaining = append(remaining, manifest)
		}
	}
	ses.StagedManifests = remaining
	ses.UpdatedAt = time.Now()
	if err := session.Save(s.StorageDir, ses); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.publishLocked(ses)
	writeJSON(w, 200, map[string]string{"status": "discarded", "manifest_id": id})
}
