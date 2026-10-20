package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/session"
)

func (s *Server) removeWorkspace(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	s.mu.Lock()
	defer s.mu.Unlock()
	for sessionID, ses := range s.sessions {
		if ses.WorkspaceID == id && (s.cancels[sessionID] != nil || s.uploading[sessionID]) {
			writeJSON(w, 409, map[string]string{"error": "工作区正在处理文件或分析，完成后再移除"})
			return
		}
	}
	active, err := session.List(s.StorageDir)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	deleted, err := session.ListDeleted(s.StorageDir)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	var affected []*agent.Session
	for _, meta := range append(active, deleted...) {
		if meta.WorkspaceID != id {
			continue
		}
		ses, ok := s.sessions[meta.ID]
		if !ok {
			ses, err = session.Load(s.StorageDir, meta.ID, s.Cfg.Skills)
			if err != nil {
				writeJSON(w, 500, map[string]string{"error": err.Error()})
				return
			}
		}
		affected = append(affected, ses)
	}
	request, err := http.NewRequestWithContext(r.Context(), "DELETE", s.Worker.BaseURL+"/workspaces/"+url.PathEscape(id), nil)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	response, err := s.Worker.HTTP.Do(request)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(response.StatusCode)
		io.Copy(w, response.Body)
		return
	}
	var record json.RawMessage
	if err := json.NewDecoder(response.Body).Decode(&record); err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	for _, ses := range affected {
		ses.WorkspaceID = ""
		if err := session.Save(s.StorageDir, ses); err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		s.sessions[ses.ID] = ses
		s.publishLocked(ses)
	}
	writeJSON(w, 200, map[string]any{"workspace": record, "detached_sessions": len(affected)})
}

func (s *Server) workspaceProxy(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Method == "POST" {
		var err error
		body, err = io.ReadAll(r.Body)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		var operation struct {
			Action string `json:"action"`
		}
		if err = json.Unmarshal(body, &operation); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		if operation.Action != "" && operation.Action != "exclude" && operation.Action != "include" {
			s.mu.Lock()
			busy := false
			identifier := strings.Split(r.URL.Path, "/")[2]
			for id := range s.cancels {
				if s.sessions[id].WorkspaceID == identifier {
					busy = true
				}
			}
			s.mu.Unlock()
			if busy {
				writeJSON(w, 409, map[string]string{"error": "工作区正在分析中，完成后再整理原文件"})
				return
			}
		}
	}
	request, err := http.NewRequestWithContext(r.Context(), r.Method, s.Worker.BaseURL+r.URL.RequestURI(), bytes.NewReader(body))
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Range", r.Header.Get("Range"))
	response, err := s.Worker.HTTP.Do(request)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	defer response.Body.Close()
	if r.Method == "POST" && response.StatusCode < 300 {
		result, err := io.ReadAll(response.Body)
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
			return
		}
		var relocated struct {
			Moves []contracts.FileMove `json:"relocations"`
		}
		json.Unmarshal(result, &relocated)
		if len(relocated.Moves) > 0 {
			s.mu.Lock()
			for id, ses := range s.sessions {
				if s.cancels[id] == nil {
					changed := contracts.UpdatePaths(ses.Manifests, relocated.Moves)
					staged := contracts.UpdatePaths(ses.StagedManifests, relocated.Moves)
					if changed || staged {
						s.persist(ses)
						s.publishLocked(ses)
					}
				}
			}
			s.mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(result)
		return
	}
	for _, header := range []string{"Content-Type", "Content-Disposition", "Content-Length", "Content-Range", "Accept-Ranges"} {
		if value := response.Header.Get(header); value != "" {
			w.Header().Set(header, value)
		}
	}
	w.WriteHeader(response.StatusCode)
	io.Copy(w, response.Body)
}

func (s *Server) selectWorkspace(w http.ResponseWriter, r *http.Request) {
	ses := s.get(r.PathValue("id"))
	if ses == nil {
		http.NotFound(w, r)
		return
	}
	var input struct {
		ID string `json:"workspace_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	if input.ID != "" {
		_, status, err := s.Worker.Get(r.Context(), "/workspaces/"+url.PathEscape(input.ID))
		if err != nil || status != 200 {
			writeJSON(w, 400, map[string]string{"error": "无法打开所选工作区"})
			return
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cancels[ses.ID] != nil {
		writeJSON(w, 409, map[string]string{"error": "当前分析结束后再切换工作区"})
		return
	}
	ses.WorkspaceID = input.ID
	ses.UpdatedAt = time.Now()
	if err := session.Save(s.StorageDir, ses); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.publishLocked(ses)
	writeJSON(w, 200, map[string]string{"workspace_id": input.ID})
}
