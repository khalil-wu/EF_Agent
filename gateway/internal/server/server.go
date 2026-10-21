// Package server exposes the gateway over HTTP + SSE.
//
//	GET  /                               web UI
//	GET  /sessions                       -> [{session_id,title,updated_at,...}]
//	POST /sessions                       -> {session_id}
//	GET  /sessions/{id}                  -> transcript + manifests + artifacts (loads from disk if not in memory)
//	POST /sessions/{id}/attachments      multipart file -> manifest + preview
//	POST /sessions/{id}/messages         {"text","mode"} -> text/event-stream; if the session is running, the text is queued as steering
//	GET  /artifacts/{id}, /artifacts/{id}/file, /skills, /health
package server

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/artifacts"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/worker"
)

//go:embed static/*
var static embed.FS

var staticAssetRef = regexp.MustCompile(`(/static/[^"?]+)"`)

type Server struct {
	Cfg               agent.Config
	Worker            *worker.Client
	StorageDir        string
	ModelName         string
	ModelProxy        string
	ModelCapabilities map[string]llm.ModelCapability
	BuildID           string
	SourceRoot        string
	ConfigID          string
	mu                sync.Mutex
	sessions          map[string]*agent.Session
	views             map[string]json.RawMessage // immutable UI snapshots, published by the session writer
	uploading         map[string]bool
	cancels           map[string]context.CancelFunc // session_id -> cancel of the running agent
	live              map[string]*liveRun
}

func New(cfg agent.Config, w *worker.Client, storageDir string) *Server {
	return &Server{Cfg: cfg, Worker: w, StorageDir: storageDir, sessions: map[string]*agent.Session{}, views: map[string]json.RawMessage{}, uploading: map[string]bool{}, cancels: map[string]context.CancelFunc{}, live: map[string]*liveRun{}}
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	assets, _ := fs.Sub(static, "static")
	if dir := os.Getenv("EEGAGENT_STATIC_DIR"); dir != "" {
		assets = os.DirFS(dir)
	}
	files := http.StripPrefix("/static/", http.FileServer(http.FS(assets)))
	mux.Handle("GET /static/", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-cache")
		files.ServeHTTP(w, r)
	}))
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, _ *http.Request) {
		b, err := fs.ReadFile(assets, "index.html")
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		version := url.QueryEscape(s.BuildID + "." + s.ConfigID)
		_, _ = w.Write(staticAssetRef.ReplaceAll(b, []byte(`${1}?v=`+version+`"`)))
	})
	mux.HandleFunc("GET /sessions", s.listSessions)
	mux.HandleFunc("/workspaces", s.workspaceProxy)
	mux.HandleFunc("/workspaces/", s.workspaceProxy)
	mux.HandleFunc("DELETE /workspaces/{id}", s.removeWorkspace)
	mux.HandleFunc("PATCH /sessions/{id}/workspace", s.selectWorkspace)
	mux.HandleFunc("POST /sessions", s.createSession)
	mux.HandleFunc("GET /sessions/{id}", s.getSession)
	mux.HandleFunc("PATCH /sessions/{id}", s.renameSession)
	mux.HandleFunc("DELETE /sessions/{id}", s.deleteSession)
	mux.HandleFunc("POST /sessions/{id}/restore", s.restoreSession)
	mux.HandleFunc("POST /sessions/{id}/clone", s.cloneSession)
	mux.HandleFunc("GET /sessions/{id}/export", s.exportSession)
	mux.HandleFunc("PATCH /sessions/{id}/skill", s.selectSkill)
	mux.HandleFunc("POST /sessions/{id}/compact", s.compactSession)
	mux.HandleFunc("POST /sessions/{id}/attachments", s.upload)
	mux.HandleFunc("DELETE /sessions/{id}/attachments/{manifest_id}", s.discardAttachment)
	mux.HandleFunc("GET /sessions/{id}/attachments/{manifest_id}/file", s.attachmentFile)
	mux.HandleFunc("GET /sessions/{id}/attachments/{manifest_id}/preview", s.attachmentPreview)
	mux.HandleFunc("POST /sessions/{id}/messages", s.message)
	mux.HandleFunc("GET /sessions/{id}/events", s.events)
	mux.HandleFunc("POST /sessions/{id}/stop", s.stop)
	mux.HandleFunc("GET /artifacts/{id}", s.artifactRecord)
	mux.HandleFunc("GET /artifacts/{id}/file", s.artifactFile)
	mux.HandleFunc("GET /knowledge/{id}", s.knowledgeChunk)
	mux.HandleFunc("GET /sessions/{session_id}/knowledge/{id}", s.knowledgeChunk)
	mux.HandleFunc("GET /knowledge", s.knowledgeStats)
	mux.HandleFunc("GET /knowledge/documents/{digest}/file", s.knowledgeDocument)
	mux.HandleFunc("GET /models", s.modelCatalog)
	mux.HandleFunc("GET /llm/models", s.conversationModels)
	mux.HandleFunc("PATCH /sessions/{id}/model", s.selectConversationModel)
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		tools := make([]string, 0, len(s.Cfg.AllTools))
		for n := range s.Cfg.AllTools {
			tools = append(tools, n)
		}
		sort.Strings(tools)
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		body, code, err := s.Worker.Get(ctx, "/health")
		status, workerState := "ok", "ok"
		if err != nil || code != 200 {
			status, workerState = "degraded", "unavailable"
		}
		var workerHealth struct {
			Status string `json:"status"`
		}
		if err == nil && code == 200 {
			if json.Unmarshal(body, &workerHealth) != nil || workerHealth.Status != "ok" {
				status, workerState = "degraded", "degraded"
			}
		}
		result := map[string]any{"status": status, "model": s.ModelName, "tools": tools, "worker": workerState, "llm": "configured", "llm_proxy": s.ModelProxy,
			"application": "EFAgent", "component": "gateway", "build_id": s.BuildID, "source_root": s.SourceRoot,
			"storage_dir": s.StorageDir, "worker_url": s.Worker.BaseURL,
			"configuration_id": s.ConfigID, "process_id": os.Getpid(),
			"analysis_configuration": map[string]any{"conditions": s.Cfg.Ablations, "skills_enabled": len(s.Cfg.Skills) > 0, "worker_tools": tools}}
		if err == nil && json.Valid(body) {
			result["worker_details"] = json.RawMessage(body)
		}
		writeJSON(w, 200, result)
	})
	mux.HandleFunc("GET /skills", func(w http.ResponseWriter, _ *http.Request) {
		type row struct {
			Name        string   `json:"name"`
			Description string   `json:"description"`
			Modalities  []string `json:"modalities"`
			Tools       []string `json:"allowed_tools"`
			Unavailable []string `json:"unavailable_tools"`
			Note        string   `json:"note,omitempty"`
		}
		out := []row{}
		for _, sk := range s.Cfg.Skills {
			entry := row{Name: sk.Name, Description: sk.Description, Modalities: sk.Modalities}
			for _, name := range sk.AllowedTools {
				if _, ok := s.Cfg.AllTools[name]; ok {
					entry.Tools = append(entry.Tools, name)
				} else {
					entry.Unavailable = append(entry.Unavailable, name)
				}
			}
			if len(sk.Models) > 0 {
				if _, ok := s.Cfg.AllTools["run_model"]; !ok {
					entry.Note = "当前可执行特征分析，模型推理与视频执行器尚未接入"
				}
			}
			out = append(out, entry)
		}
		writeJSON(w, 200, out)
	})
	return mux
}

// ---------------------------------------------------------------- sessions

func (s *Server) listSessions(w http.ResponseWriter, r *http.Request) {
	list, err := session.List(s.StorageDir)
	if r.URL.Query().Get("view") == "trash" {
		list, err = session.ListDeleted(s.StorageDir)
	}
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	s.mu.Lock()
	for i := range list {
		list[i].Running = s.cancels[list[i].ID] != nil
	}
	s.mu.Unlock()
	writeJSON(w, 200, list)
}

func (s *Server) createSession(w http.ResponseWriter, r *http.Request) {
	id := newID("ses")
	ses := &agent.Session{ID: id, Mode: contracts.ModeReadonly, LLMModel: s.ModelName, CreatedAt: time.Now(), UpdatedAt: time.Now()}
	s.mu.Lock()
	s.sessions[id] = ses
	s.publishLocked(ses)
	s.mu.Unlock()
	if err := session.Save(s.StorageDir, ses); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]string{"session_id": id, "llm_model": ses.LLMModel})
}

// get returns the in-memory session, loading it from disk on first touch.
func (s *Server) get(id string) *agent.Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	ses := s.getLocked(id)
	if ses != nil && ses.DeletedAt != nil {
		return nil
	}
	return ses
}

func (s *Server) getLocked(id string) *agent.Session {
	if ses, ok := s.sessions[id]; ok {
		return ses
	}
	ses, err := session.Load(s.StorageDir, id, s.Cfg.Skills)
	if err != nil {
		return nil
	}
	s.sessions[id] = ses
	s.publishLocked(ses)
	return ses
}

func (s *Server) persist(ses *agent.Session) {
	if err := session.Save(s.StorageDir, ses); err != nil {
		log.Printf("save session %s: %v", ses.ID, err)
	}
}

func (s *Server) getSession(w http.ResponseWriter, r *http.Request) {
	ses := s.get(r.PathValue("id"))
	if ses == nil {
		writeJSON(w, 404, map[string]string{"error": "no such session"})
		return
	}
	s.mu.Lock()
	if ses.DeletedAt != nil {
		s.mu.Unlock()
		writeJSON(w, 404, map[string]string{"error": "会话已删除"})
		return
	}
	body := s.views[ses.ID]
	if run := s.live[ses.ID]; run != nil && s.cancels[ses.ID] != nil {
		var view map[string]json.RawMessage
		_ = json.Unmarshal(body, &view)
		view["live"], _ = json.Marshal(map[string]any{"run_id": run.id, "started_at": run.startedAt, "sequence": len(run.events), "events": run.events[run.base:]})
		body, _ = json.Marshal(view)
	}
	s.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(body)
}

// Called only by the session's writer, with the server mutex held. Readers never touch live agent state.
func (s *Server) publishLocked(ses *agent.Session) {
	transcript := append(append([]llm.Message{}, ses.ArchivedMessages...), ses.Messages...)
	body, err := json.Marshal(map[string]any{"session_id": ses.ID, "title": ses.Title, "mode": ses.Mode,
		"messages": transcript, "manifests": ses.Manifests, "staged_manifests": ses.StagedManifests, "artifacts": ses.Artifacts, "summaries": ses.Summaries,
		"context":   map[string]any{"estimated_tokens": agent.EstimateTokens(ses.Messages), "active_messages": len(ses.Messages), "archived_messages": len(ses.ArchivedMessages)},
		"usage":     ses.Usage,
		"knowledge": ses.Knowledge, "sources": ses.Sources, "knowledge_facts": ses.KnowledgeFacts, "scratch_files": ses.ScratchFiles,
		"active_skill": skillName(ses), "running": s.cancels[ses.ID] != nil, "previews": ses.Previews,
		"pending_input": ses.PendingInput,
		"llm_model":     s.conversationModel(ses), "workspace_id": ses.WorkspaceID,
		"last_error": ses.LastError, "last_stopped": ses.LastStopped, "parent_session_id": ses.ParentSessionID, "deleted_at": ses.DeletedAt, "created_at": ses.CreatedAt, "updated_at": ses.UpdatedAt})
	if err != nil {
		panic(err)
	}
	s.views[ses.ID] = body
}

func skillName(s *agent.Session) string {
	if s.ActiveSkill == nil {
		return ""
	}
	return s.ActiveSkill.Name
}

// ---------------------------------------------------------------- upload

// sidecarHeader maps data/marker files to the header file that references them. Multi-file formats only work when
// the files keep their original names side by side, so each upload group has its own directory.
var sidecarHeader = map[string]string{".fdt": ".set", ".vmrk": ".vhdr", ".eeg": ".vhdr"}
var uploadGroupID = regexp.MustCompile(`^upl_[0-9a-f-]{8,64}$`)

func (s *Server) attachmentFile(w http.ResponseWriter, r *http.Request) {
	attachment, err := s.resolveAttachment(r)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if r.URL.Query().Get("download") == "1" {
		name := attachment.Manifest.OriginalName
		if name == "" {
			name = filepath.Base(attachment.Manifest.SourcePath)
		}
		w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": name}))
	}
	path := attachment.Manifest.SourcePath
	if attachment.Manifest.Modality == "image" && r.URL.Query().Get("view") == "1" && r.URL.Query().Get("download") != "1" {
		path = attachment.Manifest.VisionPath
		w.Header().Set("Content-Type", attachment.Manifest.MediaType)
	}
	http.ServeFile(w, r, path)
}

func (s *Server) upload(w http.ResponseWriter, r *http.Request) {
	ses := s.get(r.PathValue("id"))
	if ses == nil {
		writeJSON(w, 404, map[string]string{"error": "no such session"})
		return
	}
	s.mu.Lock()
	if ses.DeletedAt != nil {
		s.mu.Unlock()
		writeJSON(w, 404, map[string]string{"error": "会话已删除"})
		return
	}
	if s.cancels[ses.ID] != nil || s.uploading[ses.ID] {
		s.mu.Unlock()
		writeJSON(w, 409, map[string]string{"error": "请等待当前分析或上传完成后再添加文件"})
		return
	}
	s.uploading[ses.ID] = true
	s.mu.Unlock()
	defer func() { s.mu.Lock(); delete(s.uploading, ses.ID); s.publishLocked(ses); s.mu.Unlock() }()
	r.Body = http.MaxBytesReader(w, r.Body, 1<<30)
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeJSON(w, 413, map[string]string{"error": "单个文件超过 1 GB 上传上限；大文件请挂接所在文件夹为工作区后直接读取"})
			return
		}
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	defer r.MultipartForm.RemoveAll()
	files := r.MultipartForm.File["file"]
	if len(files) == 0 {
		writeJSON(w, 400, map[string]string{"error": "missing form field 'file'"})
		return
	}
	group := r.FormValue("upload_group_id")
	if group == "" {
		group = newID("upl")
	}
	if !uploadGroupID.MatchString(group) {
		writeJSON(w, 400, map[string]string{"error": "无效的上传组 id"})
		return
	}
	dir := filepath.Join(s.StorageDir, "uploads", ses.ID, group)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	hdr := files[0]
	var final string
	for _, member := range files {
		file, err := member.Open()
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		path, err := storeUpload(dir, safeName(member.Filename), file)
		file.Close()
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		ext := strings.ToLower(filepath.Ext(path))
		if final == "" || ext == ".vhdr" || ext == ".ahdr" || ext == ".set" {
			final, hdr = path, member
		}
	}
	ext := strings.ToLower(filepath.Ext(final))
	target := final
	if header, ok := sidecarHeader[ext]; ok {
		main := strings.TrimSuffix(final, filepath.Ext(final)) + header
		switch {
		case fileExists(main):
			target = main // the header arrived first: read it now that its data file is here
		case ext == ".eeg":
			// no BrainVision header: may be a standalone Nihon Kohden .eeg, try it below
		default:
			writeJSON(w, 200, map[string]any{"sidecar": true, "path": final, "original_name": hdr.Filename, "upload_group_id": group,
				"note": fmt.Sprintf("已保存附属文件 %s；添加对应头文件后会一起读取", hdr.Filename)})
			return
		}
	}

	// The worker routes by content: signal files become EEG records, video and documents (including label or event
	// tables) become attachments. Acquisition facts given at upload apply to signal files.
	args := map[string]any{"path": target, "_session_id": ses.ID}
	if v := r.FormValue("sample_rate_hz"); v != "" {
		sr, err := strconv.ParseFloat(v, 64)
		if err != nil || sr <= 0 {
			writeJSON(w, 400, map[string]string{"error": "采样率必须是大于 0 的数字"})
			return
		}
		args["sample_rate_hz"] = sr
	}
	if v := r.FormValue("channel_names"); v != "" {
		args["channel_names"] = strings.Split(v, ",")
	}
	if v := r.FormValue("trial_index"); v != "" {
		ti, err := strconv.Atoi(v)
		if err != nil || ti < 0 {
			writeJSON(w, 400, map[string]string{"error": "试次必须是非负整数"})
			return
		}
		args["trial_index"] = ti
	}
	raw, _ := json.Marshal(args)
	toolName := "register_upload"
	body, err := s.Worker.Call(r.Context(), toolName, raw)
	if err != nil {
		if strings.ToLower(filepath.Ext(target)) == ".eeg" {
			writeJSON(w, 200, map[string]any{"sidecar": true, "path": final, "original_name": hdr.Filename, "upload_group_id": group,
				"note": "已保存 .eeg 文件；BrainVision 数据还需要对应的头文件和标记文件一起读取"})
			return
		}
		writeJSON(w, 502, map[string]any{"error": toolName + " failed", "detail": errString(err), "worker": workerJSON(body)})
		return
	}
	var out struct {
		Manifest *contracts.Manifest `json:"manifest"`
	}
	if err := json.Unmarshal(body, &out); err != nil || out.Manifest == nil {
		writeJSON(w, 502, map[string]any{"error": "bad manifest from worker", "worker": workerJSON(body)})
		return
	}
	displayName := hdr.Filename
	if target != final {
		displayName = filepath.Base(target)
	}
	out.Manifest.OriginalName = displayName
	out.Manifest.UploadGroupID = group
	staged := r.FormValue("staged") == "true"
	s.mu.Lock()
	records := ses.Manifests
	if staged {
		records = ses.StagedManifests
	}
	replaced := false
	for i, m := range records {
		if m.SameRecord(out.Manifest) {
			records[i] = out.Manifest
			replaced = true
		}
	}
	if !replaced {
		records = append(records, out.Manifest)
	}
	if staged {
		ses.StagedManifests = records
	} else {
		ses.Manifests = records
	}
	if ses.Title == "" {
		ses.Title = displayName
	}
	s.mu.Unlock()

	resp := map[string]any{"manifest": out.Manifest, "original_name": displayName, "upload_group_id": group}
	if out.Manifest.Modality == "eeg" && out.Manifest.SampleRateHz != nil {
		pctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
		defer cancel()
		pargs, _ := json.Marshal(map[string]any{"kind": "raw", "source_id": out.Manifest.ManifestID})
		if pbody, perr := s.Worker.Call(pctx, "plot", pargs); perr == nil {
			var pres struct {
				Artifact json.RawMessage `json:"artifact"`
			}
			if json.Unmarshal(pbody, &pres) == nil && len(pres.Artifact) > 2 {
				resp["preview"] = pres.Artifact
				s.mu.Lock()
				if ses.Previews == nil {
					ses.Previews = map[string]json.RawMessage{}
				}
				ses.Previews[out.Manifest.ManifestID] = pres.Artifact
				s.mu.Unlock()
			}
		}
	}
	s.mu.Lock()
	ses.UpdatedAt = time.Now()
	s.persist(ses)
	s.mu.Unlock()
	writeJSON(w, 200, resp)
}

// ---------------------------------------------------------------- messages

func (s *Server) message(w http.ResponseWriter, r *http.Request) {
	ses := s.get(r.PathValue("id"))
	if ses == nil {
		writeJSON(w, 404, map[string]string{"error": "no such session"})
		return
	}
	var in struct {
		Text           string               `json:"text"`
		Mode           string               `json:"mode"`
		Retry          bool                 `json:"retry"`
		InputRequestID string               `json:"input_request_id"`
		Answers        map[string]string    `json:"answers"`
		Attachments    []string             `json:"attachments"`
		DisplayText    string               `json:"display_text"`
		References     []llm.InputReference `json:"references"`
	}
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil || (!in.Retry && strings.TrimSpace(in.Text) == "" && in.InputRequestID == "" && len(in.Attachments) == 0) {
		writeJSON(w, 400, map[string]string{"error": "body must be {\"text\": ...}"})
		return
	}
	if in.Mode != "" && in.Mode != string(contracts.ModeReadonly) && in.Mode != string(contracts.ModeWriteNormalized) && in.Mode != string(contracts.ModeGPU) {
		writeJSON(w, 400, map[string]string{"error": "无效的权限模式"})
		return
	}
	references, err := s.resolveReferences(r.Context(), ses.ID, in.References)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	var presentation *llm.InputPresentation
	if len(references) > 0 {
		text := in.DisplayText
		if text == "" {
			text = in.Text
		}
		presentation = &llm.InputPresentation{Text: text, References: references}
	}
	fl, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, 500, map[string]string{"error": "streaming unsupported"})
		return
	}
	s.mu.Lock()
	if ses.DeletedAt != nil {
		s.mu.Unlock()
		writeJSON(w, 404, map[string]string{"error": "会话已删除"})
		return
	}
	if s.uploading[ses.ID] {
		s.mu.Unlock()
		writeJSON(w, 409, map[string]string{"error": "文件正在读取，请稍后发送"})
		return
	}
	if s.hasImageInput(ses, in.Attachments) && !s.ModelCapabilities[s.conversationModel(ses)].ImageInput {
		s.mu.Unlock()
		writeJSON(w, 400, map[string]any{"error": "图片问答请先选择已验证的看图模型。", "code": "image_model_required", "image_models": s.imageModels()})
		return
	}
	if s.cancels[ses.ID] != nil {
		if in.Retry {
			s.mu.Unlock()
			writeJSON(w, 409, map[string]string{"error": "当前请求仍在运行"})
			return
		}
		ses.Steer(in.Text+referenceContext(references), presentation)
		s.mu.Unlock()
		writeJSON(w, 202, map[string]string{"status": "queued"})
		return
	}
	if in.Retry {
		if ses.LastError == "" && !ses.LastStopped {
			s.mu.Unlock()
			writeJSON(w, 409, map[string]string{"error": "没有需要继续的失败或已停止请求"})
			return
		}
		in.Text = "" // Resume the retained transcript; do not append the same user message again.
	}
	if len(in.Attachments) > 0 {
		imagesOnly := true
		text := in.DisplayText
		if text == "" {
			text = in.Text
		}
		if presentation == nil {
			presentation = &llm.InputPresentation{Text: text}
		}
		for _, id := range in.Attachments {
			found := false
			for _, manifest := range append(append([]*contracts.Manifest(nil), ses.Manifests...), ses.StagedManifests...) {
				if manifest.ManifestID == id {
					imagesOnly = imagesOnly && manifest.Modality == "image"
					referenced := false
					for _, reference := range references {
						referenced = referenced || reference.ManifestID == id
					}
					if !referenced {
						presentation.Attachments = append(presentation.Attachments, llm.InputAttachment{ID: id, Name: manifest.OriginalName, Modality: manifest.Modality})
					}
					found = true
					break
				}
			}
			if !found {
				s.mu.Unlock()
				writeJSON(w, 400, map[string]string{"error": "附件不属于本会话"})
				return
			}
		}
		if strings.TrimSpace(in.Text) == "" {
			in.Text = "请检查本次上传的数据并概述主要发现。"
			if imagesOnly {
				in.Text = "请解释本次上传的图片，结合图中的标题、坐标、图例和关键变化回答。"
			}
			presentation.Text = in.Text
		}
	}
	if in.InputRequestID != "" {
		if ses.PendingInput == nil || ses.PendingInput.ID != in.InputRequestID {
			s.mu.Unlock()
			writeJSON(w, 409, map[string]string{"error": "这个问题已结束，请查看当前问题"})
			return
		}
		text, err := ses.PendingInput.AnswerText(in.Answers)
		if err != nil {
			s.mu.Unlock()
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		in.Text = text
		presentation = &llm.InputPresentation{}
		var answers []string
		for _, question := range ses.PendingInput.Questions {
			presentation.Questions = append(presentation.Questions, question.Question)
			answers = append(answers, strings.TrimSpace(in.Answers[question.ID]))
		}
		presentation.Text = strings.Join(answers, "\n")
	}
	if len(references) > 0 {
		presentation.References = references
		in.Text += referenceContext(references)
	}
	// Commit only the drafts explicitly included in this submitted message.
	remaining := ses.StagedManifests[:0]
	for _, manifest := range ses.StagedManifests {
		selected := false
		for _, id := range in.Attachments {
			selected = selected || manifest.ManifestID == id
		}
		if !selected {
			remaining = append(remaining, manifest)
			continue
		}
		replaced := false
		for i, previous := range ses.Manifests {
			if previous.SameRecord(manifest) {
				ses.Manifests[i] = manifest
				replaced = true
			}
		}
		if !replaced {
			ses.Manifests = append(ses.Manifests, manifest)
		}
	}
	ses.StagedManifests = remaining
	if err := session.Save(s.StorageDir, ses); err != nil {
		s.mu.Unlock()
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
	stopCtx, stop := context.WithCancel(ctx)
	s.cancels[ses.ID] = stop
	s.live[ses.ID] = &liveRun{id: newID("run"), startedAt: time.Now(), changed: make(chan struct{})}
	if in.Mode != "" {
		ses.Mode = contracts.PermissionMode(in.Mode)
	}
	s.publishLocked(ses)
	s.mu.Unlock()
	defer cancel()
	defer stop()
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(200)

	var sentError bool
	emit := func(ev agent.Event) {
		s.mu.Lock()
		run := s.live[ses.ID]
		ev.RunID, ev.Sequence = run.id, uint64(len(run.events)+1)
		run.events = append(run.events, ev)
		sentError = sentError || ev.Type == "error"
		if ev.Type == "error" {
			ses.LastError = ev.Text
		}
		if ev.Type == "stopped" {
			ses.LastStopped = true
		}
		switch ev.Type {
		case "agent_start", "message_completed", "turn_end", "artifact_ready", "knowledge_retrieved", "skill_activated", "user_input_requested", "compaction_end", "error", "stopped":
			s.publishLocked(ses)
		}
		switch ev.Type {
		case "agent_start", "message_completed", "turn_end", "compaction_end":
			run.base = len(run.events)
		}
		if ev.Type == "session_saved" {
			run.done = true
		}
		close(run.changed)
		run.changed = make(chan struct{})
		s.mu.Unlock()
		_, _ = writeEvent(w, ev)
		fl.Flush()
	}
	next := in.Text
	cfg := s.Cfg
	cfg.Checkpoint = func(ses *agent.Session) error { return session.Save(s.StorageDir, ses) }
	cfg.LLM = s.Cfg.LLM.WithModel(ses.LLMModel)
	cfg.InputPresentation = presentation
	for {
		_, runErr := agent.Run(stopCtx, cfg, ses, next, withStop(stopCtx, emit))
		cfg.InputPresentation = nil
		if runErr != nil {
			if stopCtx.Err() == nil {
				log.Printf("session %s model=%s proxy=%s failed: %v", ses.ID, cfg.LLM.Model, s.ModelProxy, runErr)
			}
			switch {
			case stopCtx.Err() == context.Canceled && ctx.Err() == nil:
				emit(agent.Event{Type: "stopped", Timestamp: time.Now()})
			case !sentError: // the loop already reported stream errors; don't show them twice
				emit(agent.Event{Type: "error", Timestamp: time.Now(), Text: runErr.Error()})
			}
		}
		s.mu.Lock()
		pending := ses.DrainSteering()
		if len(pending) > 0 && runErr == nil && stopCtx.Err() == nil {
			next = pending[0].Content
			cfg.InputPresentation = pending[0].Presentation
			for _, text := range pending[1:] {
				ses.Steer(text.Content, text.Presentation)
			}
			s.mu.Unlock()
			continue
		}
		for _, text := range pending {
			ses.Messages = append(ses.Messages, text)
		}
		s.persist(ses)
		delete(s.cancels, ses.ID)
		s.publishLocked(ses)
		s.mu.Unlock()
		break
	}
	emit(agent.Event{Type: "session_saved", Timestamp: time.Now()})
}

// withStop hides the loop's own error event when the user pressed stop; the caller reports "stopped".
func withStop(ctx context.Context, emit func(agent.Event)) func(agent.Event) {
	return func(ev agent.Event) {
		if ev.Type == "error" && ctx.Err() == context.Canceled {
			return
		}
		emit(ev)
	}
}

func (s *Server) stop(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	cancel := s.cancels[r.PathValue("id")]
	s.mu.Unlock()
	if cancel == nil {
		writeJSON(w, 409, map[string]string{"error": "not running"})
		return
	}
	cancel()
	writeJSON(w, 200, map[string]string{"status": "stopping"})
}

// ---------------------------------------------------------------- artifacts

func (s *Server) artifactRecord(w http.ResponseWriter, r *http.Request) {
	rec, err := artifacts.Load(s.StorageDir, r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, rec)
}

func (s *Server) artifactFile(w http.ResponseWriter, r *http.Request) {
	rec, err := artifacts.Load(s.StorageDir, r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	uri, mediaType := rec.URI, rec.MediaType
	if format := r.URL.Query().Get("format"); format != "" {
		representation, ok := rec.Representations[format]
		if !ok {
			writeJSON(w, 404, map[string]string{"error": "该产物没有此格式"})
			return
		}
		uri, mediaType = representation.URI, representation.MediaType
	}
	abs, err := filepath.Abs(uri)
	root, _ := filepath.Abs(s.StorageDir)
	rel, relErr := filepath.Rel(root, abs)
	if err != nil || relErr != nil || !filepath.IsLocal(rel) {
		writeJSON(w, 403, map[string]string{"error": "artifact outside storage"})
		return
	}
	if mediaType != "" {
		w.Header().Set("Content-Type", mediaType)
	}
	if rec.Kind == "report" && mediaType == "text/markdown" && r.URL.Query().Get("download") == "1" {
		body, err := os.ReadFile(abs)
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": err.Error()})
			return
		}
		w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": artifacts.DownloadFilename(s.StorageDir, rec, filepath.Ext(abs))}))
		_, _ = w.Write([]byte(localArtifactLinks(string(body), r.Host)))
		return
	}
	if rec.Kind == "table" || r.URL.Query().Get("download") == "1" {
		w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": artifacts.DownloadFilename(s.StorageDir, rec, filepath.Ext(abs))}))
	}
	http.ServeFile(w, r, abs)
}

var artifactURLRe = regexp.MustCompile(`artifact://(art_[0-9a-f]+)`)

func localArtifactLinks(markdown, host string) string {
	return artifactURLRe.ReplaceAllString(markdown, "http://"+host+"/artifacts/$1/file")
}

// ---------------------------------------------------------------- knowledge (proxied to the worker)

func (s *Server) modelCatalog(w http.ResponseWriter, r *http.Request) {
	body, code, err := s.Worker.Get(r.Context(), "/models")
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(body)
}

var chunkIDRe = regexp.MustCompile(`^k_[0-9a-f]{6,}$`)

func (s *Server) knowledgeChunk(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !chunkIDRe.MatchString(id) {
		writeJSON(w, 400, map[string]string{"error": "bad chunk id"})
		return
	}
	path := "/knowledge/" + id
	if sessionID := r.PathValue("session_id"); sessionID != "" {
		if s.get(sessionID) == nil {
			writeJSON(w, 404, map[string]string{"error": "会话不存在"})
			return
		}
		s.mu.Lock()
		view := s.views[sessionID]
		s.mu.Unlock()
		var snapshot struct {
			Sources map[string]json.RawMessage `json:"sources"`
		}
		if err := json.Unmarshal(view, &snapshot); err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		if source, found := snapshot.Sources[id]; found {
			writeJSON(w, 200, source)
			return
		}
		writeJSON(w, 404, map[string]string{"error": "此会话未保留该引用的原文摘录"})
		return
	}
	body, code, err := s.Worker.Get(r.Context(), path)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(body)
}

func (s *Server) knowledgeStats(w http.ResponseWriter, r *http.Request) {
	body, code, err := s.Worker.Get(r.Context(), "/knowledge")
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(body)
}

var documentDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)

func (s *Server) knowledgeDocument(w http.ResponseWriter, r *http.Request) {
	digest := r.PathValue("digest")
	if !documentDigest.MatchString(digest) {
		http.NotFound(w, r)
		return
	}
	ext := ".md"
	switch r.URL.Query().Get("format") {
	case "":
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	case "pdf":
		ext = ".pdf"
		w.Header().Set("Content-Type", "application/pdf")
	case "html":
		ext = ".html"
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Content-Disposition", `attachment; filename="source.html"`)
	default:
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.StorageDir, "knowledge_sources", digest+ext))
}

// ---------------------------------------------------------------- utils

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func newID(prefix string) string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return prefix + "_" + hex.EncodeToString(b)
}

// safeName keeps the uploaded file name (multi-file formats reference each other by name) but drops path parts and
// characters that are not allowed in file names on Windows.
func safeName(name string) string {
	name = filepath.Base(strings.ReplaceAll(name, "\\", "/"))
	var b strings.Builder
	for _, r := range name {
		if r < 32 || strings.ContainsRune(`<>:"/\|?*`, r) {
			b.WriteRune('_')
			continue
		}
		b.WriteRune(r)
	}
	out := strings.TrimRight(strings.TrimSpace(b.String()), ". ")
	if out == "" || out == "." || out == ".." {
		return "file"
	}
	return out
}

// storeUpload writes the upload as dir/name. An identical existing file is reused; a different one with the same
// name gets a random suffix (its sidecars then keep their own names, so re-upload them together if needed).
func storeUpload(dir, name string, src io.Reader) (string, error) {
	tmp, err := os.CreateTemp(dir, ".upload-*")
	if err != nil {
		return "", err
	}
	h := sha256.New()
	if _, err := io.Copy(io.MultiWriter(tmp, h), src); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return "", err
	}
	tmp.Close()
	final := filepath.Join(dir, name)
	if fileExists(final) {
		if sum, err := fileSHA256(final); err == nil && sum == hex.EncodeToString(h.Sum(nil)) {
			os.Remove(tmp.Name())
			return final, nil
		}
		os.Remove(tmp.Name())
		return "", fmt.Errorf("同一上传组包含同名不同内容的文件 %s；请为新记录使用新的 upload_group_id", name)
	}
	if err := os.Rename(tmp.Name(), final); err != nil {
		os.Remove(tmp.Name())
		return "", err
	}
	return final, nil
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func fileSHA256(p string) (string, error) {
	f, err := os.Open(p)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func workerJSON(body []byte) json.RawMessage {
	if len(body) == 0 {
		return nil
	}
	if json.Valid(body) {
		return body
	}
	b, _ := json.Marshal(string(body))
	return b
}
func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
