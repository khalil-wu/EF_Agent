package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
)

// Resolve selected paths through the mounted folder; uploaded files retain their session identity.
func (s *Server) resolveReferences(ctx context.Context, id string, input []llm.InputReference) ([]llm.InputReference, error) {
	if len(input) == 0 {
		return nil, nil
	}
	s.mu.Lock()
	view := s.views[id]
	s.mu.Unlock()
	var snapshot struct {
		WorkspaceID string                `json:"workspace_id"`
		Manifests   []*contracts.Manifest `json:"manifests"`
		Staged      []*contracts.Manifest `json:"staged_manifests"`
	}
	if err := json.Unmarshal(view, &snapshot); err != nil {
		return nil, err
	}
	query := url.Values{}
	for _, item := range input {
		if item.ManifestID == "" {
			if item.WorkspaceID == "" || item.WorkspaceID != snapshot.WorkspaceID {
				return nil, fmt.Errorf("请选择当前工作区中的文件或文件夹")
			}
			query.Add("path", item.Path)
		}
	}
	var workspace struct {
		Items []llm.InputReference `json:"items"`
	}
	if len(query) > 0 {
		body, status, err := s.Worker.Get(ctx, "/workspaces/"+url.PathEscape(snapshot.WorkspaceID)+"/references?"+query.Encode())
		if err != nil {
			return nil, err
		}
		if status != 200 {
			return nil, fmt.Errorf("引用的文件或文件夹已不可用，请重新选择")
		}
		if err := json.Unmarshal(body, &workspace); err != nil {
			return nil, err
		}
	}
	result := []llm.InputReference{}
	for _, item := range input {
		if item.ManifestID == "" {
			result = append(result, workspace.Items[0])
			workspace.Items = workspace.Items[1:]
			continue
		}
		var found *contracts.Manifest
		for _, m := range append(snapshot.Manifests, snapshot.Staged...) {
			if m.ManifestID == item.ManifestID {
				found = m
				break
			}
		}
		if found == nil {
			return nil, fmt.Errorf("引用的文件不属于本会话")
		}
		result = append(result, llm.InputReference{ManifestID: found.ManifestID, Name: found.OriginalName, Kind: "file"})
	}
	return result, nil
}

func referenceContext(references []llm.InputReference) string {
	if len(references) == 0 {
		return ""
	}
	body, _ := json.Marshal(references)
	return "\n\n<file_references>\n用户为本次任务明确选择的范围如下。工作区文件的 path 是实际相对路径，目录 path 为 . 表示整个当前工作区。围绕这些输入和用户目标读取说明、自主选择方法并完成必要的计算与结果保存。目录引用确定任务范围，实际覆盖以执行结果为准；文件类型按实际内容解析。\n" + string(body) + "\n</file_references>"
}
