package server

import (
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"eegagent/gateway/internal/session"
)

func (s *Server) renameSession(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Title string `json:"title"`
	}
	if json.NewDecoder(r.Body).Decode(&in) != nil {
		writeJSON(w, 400, map[string]string{"error": "需要会话名称"})
		return
	}
	in.Title = strings.TrimSpace(in.Title)
	if in.Title == "" || len([]rune(in.Title)) > 120 {
		writeJSON(w, 400, map[string]string{"error": "名称需为 1–120 个字符"})
		return
	}
	s.changeSession(w, r, &in.Title, nil)
}

func (s *Server) deleteSession(w http.ResponseWriter, r *http.Request) {
	deleted := true
	s.changeSession(w, r, nil, &deleted)
}
func (s *Server) restoreSession(w http.ResponseWriter, r *http.Request) {
	deleted := false
	s.changeSession(w, r, nil, &deleted)
}

func (s *Server) changeSession(w http.ResponseWriter, r *http.Request, title *string, deleted *bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	ses := s.getLocked(r.PathValue("id"))
	restoring := deleted != nil && !*deleted
	if ses == nil || (ses.DeletedAt != nil && !restoring) {
		writeJSON(w, 404, map[string]string{"error": "会话不存在或已删除"})
		return
	}
	if s.cancels[ses.ID] != nil || s.uploading[ses.ID] {
		writeJSON(w, 409, map[string]string{"error": "请等待当前分析或文件操作完成后再管理会话"})
		return
	}
	if restoring && ses.DeletedAt == nil {
		writeJSON(w, 409, map[string]string{"error": "会话不在回收站中"})
		return
	}
	oldTitle, oldDeleted, oldUpdated := ses.Title, ses.DeletedAt, ses.UpdatedAt
	now := time.Now()
	ses.UpdatedAt = now
	if title != nil {
		ses.Title = *title
	}
	if deleted != nil {
		if *deleted {
			ses.DeletedAt = &now
		} else {
			ses.DeletedAt = nil
		}
	}
	if err := session.Save(s.StorageDir, ses); err != nil {
		ses.Title, ses.DeletedAt, ses.UpdatedAt = oldTitle, oldDeleted, oldUpdated
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.publishLocked(ses)
	writeJSON(w, 200, map[string]any{"session_id": ses.ID, "title": ses.Title, "deleted_at": ses.DeletedAt})
}

func (s *Server) cloneSession(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	source := s.getLocked(r.PathValue("id"))
	if source == nil || source.DeletedAt != nil {
		s.mu.Unlock()
		writeJSON(w, 404, map[string]string{"error": "源会话不存在"})
		return
	}
	if s.cancels[source.ID] != nil || s.uploading[source.ID] {
		s.mu.Unlock()
		writeJSON(w, 409, map[string]string{"error": "请等待当前分析或文件操作完成后再复制"})
		return
	}
	// The source is idle and persisted. Load an independent copy, including nested messages and manifests.
	child, err := session.Load(s.StorageDir, source.ID, s.Cfg.Skills)
	if err != nil {
		s.mu.Unlock()
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.uploading[source.ID] = true
	s.mu.Unlock()
	defer func() { s.mu.Lock(); delete(s.uploading, source.ID); s.mu.Unlock() }()
	child.ID = newID("ses")
	child.ParentSessionID = source.ID
	child.Title = source.Title + " · 分支"
	if source.Title == "" {
		child.Title = "新分析 · 分支"
	}
	child.CreatedAt = time.Now()
	child.UpdatedAt = child.CreatedAt
	if err := copyScratch(filepath.Join(s.StorageDir, "scratch", source.ID), filepath.Join(s.StorageDir, "scratch", child.ID)); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	if err := copyScratch(filepath.Join(s.StorageDir, "documents", source.ID), filepath.Join(s.StorageDir, "documents", child.ID)); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	if err := session.Save(s.StorageDir, child); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.mu.Lock()
	s.sessions[child.ID] = child
	s.publishLocked(child)
	s.mu.Unlock()
	writeJSON(w, 201, map[string]string{"session_id": child.ID, "parent_session_id": source.ID})
}

func copyScratch(source, destination string) error {
	if _, err := os.Stat(source); os.IsNotExist(err) {
		return nil
	} else if err != nil {
		return err
	}
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		if entry.IsDir() {
			switch entry.Name() {
			case ".tmp", ".home", ".mpl", "__pycache__":
				return filepath.SkipDir
			}
			return os.MkdirAll(filepath.Join(destination, rel), 0o755)
		}
		src, err := os.Open(path)
		if err != nil {
			return err
		}
		defer src.Close()
		dst, err := os.Create(filepath.Join(destination, rel))
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(dst, src)
		closeErr := dst.Close()
		if copyErr != nil {
			return copyErr
		}
		return closeErr
	})
}

func (s *Server) exportSession(w http.ResponseWriter, r *http.Request) {
	ses := s.get(r.PathValue("id"))
	if ses == nil {
		writeJSON(w, 404, map[string]string{"error": "会话不存在"})
		return
	}
	s.mu.Lock()
	if ses.DeletedAt != nil {
		s.mu.Unlock()
		writeJSON(w, 404, map[string]string{"error": "会话已删除"})
		return
	}
	body := s.views[ses.ID]
	s.mu.Unlock()
	format := r.URL.Query().Get("format")
	if format != "md" && format != "json" {
		writeJSON(w, 400, map[string]string{"error": "导出格式仅支持 md 或 json"})
		return
	}
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=\"eegagent-%s.%s\"", ses.ID, format))
	if format == "json" {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Write(body)
		return
	}
	var snap session.Snapshot
	if err := json.Unmarshal(body, &snap); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	fmt.Fprintf(w, "# %s\n\n会话：%s\n\n", snap.Title, snap.ID)
	if snap.ParentSessionID != "" {
		fmt.Fprintf(w, "来源分析：%s\n\n", snap.ParentSessionID)
	}
	if len(snap.Manifests) > 0 {
		fmt.Fprint(w, "## 数据\n\n")
		for _, m := range snap.Manifests {
			fmt.Fprintf(w, "- %s（%s，%d 通道", m.OriginalName, m.ManifestID, m.NChannels)
			if m.SampleRateHz != nil {
				fmt.Fprintf(w, "，%g Hz", *m.SampleRateHz)
			}
			if m.DurationS != nil {
				fmt.Fprintf(w, "，%g 秒", *m.DurationS)
			}
			fmt.Fprint(w, "）\n")
		}
		fmt.Fprint(w, "\n")
	}
	if len(snap.Summaries) > 0 {
		fmt.Fprintf(w, "## 较早对话摘要\n\n%s\n\n", snap.Summaries[len(snap.Summaries)-1])
	}
	fmt.Fprint(w, "## 对话\n\n")
	for _, m := range snap.Messages {
		if (m.Role != "user" && m.Role != "assistant") || strings.TrimSpace(m.Content) == "" || strings.HasPrefix(m.Content, "<") {
			continue
		}
		role := "EFAgent"
		if m.Role == "user" {
			role = "用户"
		}
		fmt.Fprintf(w, "### %s\n\n%s\n\n", role, localArtifactLinks(m.Content, r.Host))
	}
	if len(snap.Artifacts) > 0 {
		fmt.Fprint(w, "## 结果文件\n\n")
		for _, raw := range snap.Artifacts {
			var a struct {
				ID   string `json:"artifact_id"`
				Kind string `json:"kind"`
			}
			json.Unmarshal(raw, &a)
			fmt.Fprintf(w, "- [%s · %s](http://%s/artifacts/%s/file)\n", a.Kind, a.ID, r.Host, a.ID)
		}
		fmt.Fprint(w, "\n结果链接由本地 EFAgent 服务提供；导出文档未打包二进制数据。\n")
	}
	if snap.LastError != "" {
		fmt.Fprintf(w, "\n## 最后一次请求错误\n\n%s\n", snap.LastError)
	}
}
