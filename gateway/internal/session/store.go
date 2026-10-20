// Package session persists agent sessions as JSON snapshots under storage/sessions/.
// v1 is linear (no tree, no branches); see PRD §12.1.
package session

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/skills"
)

var idRe = regexp.MustCompile(`^ses_[0-9a-f]{6,}$`)

type Snapshot struct {
	ID               string                     `json:"session_id"`
	Title            string                     `json:"title"`
	ParentSessionID  string                     `json:"parent_session_id,omitempty"`
	DeletedAt        *time.Time                 `json:"deleted_at,omitempty"`
	Mode             contracts.PermissionMode   `json:"mode"`
	LLMModel         string                     `json:"llm_model,omitempty"`
	WorkspaceID      string                     `json:"workspace_id,omitempty"`
	ActiveSkill      string                     `json:"active_skill"`
	Messages         []llm.Message              `json:"messages"`
	ArchivedMessages []llm.Message              `json:"archived_messages,omitempty"`
	Manifests        []*contracts.Manifest      `json:"manifests"`
	StagedManifests  []*contracts.Manifest      `json:"staged_manifests,omitempty"`
	Previews         map[string]json.RawMessage `json:"previews,omitempty"`
	Knowledge        []json.RawMessage          `json:"knowledge,omitempty"`
	Sources          map[string]json.RawMessage `json:"sources,omitempty"`
	KnowledgeFacts   []json.RawMessage          `json:"knowledge_facts,omitempty"`
	Artifacts        []json.RawMessage          `json:"artifacts"`
	ScratchFiles     []string                   `json:"scratch_files,omitempty"`
	Summaries        []string                   `json:"summaries"`
	Usage            []agent.TurnUsage          `json:"usage,omitempty"`
	CreatedAt        time.Time                  `json:"created_at"`
	UpdatedAt        time.Time                  `json:"updated_at"`
	LastError        string                     `json:"last_error,omitempty"`
	LastStopped      bool                       `json:"last_stopped,omitempty"`
	PendingInput     *agent.InputRequest        `json:"pending_input,omitempty"`
	Running          bool                       `json:"running,omitempty"`
}

type Meta struct {
	Version         int        `json:"version"`
	WorkspaceID     string     `json:"workspace_id,omitempty"`
	ID              string     `json:"session_id"`
	Title           string     `json:"title"`
	UpdatedAt       time.Time  `json:"updated_at"`
	NManifests      int        `json:"n_manifests"`
	NMessages       int        `json:"n_messages"`
	NArtifacts      int        `json:"n_artifacts"`
	ParentSessionID string     `json:"parent_session_id,omitempty"`
	DeletedAt       *time.Time `json:"deleted_at,omitempty"`
	Running         bool       `json:"running"`
}

func dir(storage string) string { return filepath.Join(storage, "sessions") }

func Save(storage string, s *agent.Session) error {
	if !idRe.MatchString(s.ID) {
		return errors.New("bad session id")
	}
	if err := os.MkdirAll(dir(storage), 0o755); err != nil {
		return err
	}
	snap := Snapshot{ID: s.ID, Title: s.Title, Mode: s.Mode, Messages: s.Messages, Manifests: s.Manifests, Artifacts: s.Artifacts,
		ScratchFiles: s.ScratchFiles, Summaries: s.Summaries, Usage: s.Usage, CreatedAt: s.CreatedAt, UpdatedAt: s.UpdatedAt}
	snap.Previews, snap.Knowledge, snap.KnowledgeFacts = s.Previews, s.Knowledge, s.KnowledgeFacts
	snap.StagedManifests = s.StagedManifests
	snap.Sources = s.Sources
	snap.LastError, snap.LastStopped = s.LastError, s.LastStopped
	snap.LLMModel = s.LLMModel
	snap.WorkspaceID = s.WorkspaceID
	snap.PendingInput = s.PendingInput
	snap.Running = s.Running
	snap.ParentSessionID, snap.DeletedAt = s.ParentSessionID, s.DeletedAt
	snap.ArchivedMessages = s.ArchivedMessages
	if s.ActiveSkill != nil {
		snap.ActiveSkill = s.ActiveSkill.Name
	}
	if snap.UpdatedAt.IsZero() {
		snap.UpdatedAt = time.Now()
	}
	if snap.CreatedAt.IsZero() {
		snap.CreatedAt = snap.UpdatedAt
	}
	b, err := json.MarshalIndent(snap, "", " ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(dir(storage), s.ID+".json.tmp")
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, filepath.Join(dir(storage), s.ID+".json")); err != nil {
		return err
	}
	meta := Meta{Version: 2, WorkspaceID: s.WorkspaceID, ID: s.ID, Title: s.Title, UpdatedAt: snap.UpdatedAt, NManifests: len(s.Manifests),
		NMessages: len(s.Messages), NArtifacts: len(s.Artifacts), ParentSessionID: s.ParentSessionID, DeletedAt: s.DeletedAt}
	mb, err := json.Marshal(meta)
	if err != nil {
		return err
	}
	metaTmp := filepath.Join(dir(storage), s.ID+".meta.json.tmp")
	if err := os.WriteFile(metaTmp, mb, 0o644); err != nil {
		return err
	}
	return os.Rename(metaTmp, filepath.Join(dir(storage), s.ID+".meta.json"))
}

func Load(storage, id string, sk []*skills.Skill) (*agent.Session, error) {
	if !idRe.MatchString(id) {
		return nil, errors.New("bad session id")
	}
	b, err := os.ReadFile(filepath.Join(dir(storage), id+".json"))
	if err != nil {
		return nil, err
	}
	var snap Snapshot
	if err := json.Unmarshal(b, &snap); err != nil {
		return nil, err
	}
	s := &agent.Session{ID: snap.ID, Title: snap.Title, Mode: snap.Mode, Messages: snap.Messages, Manifests: snap.Manifests,
		Artifacts: snap.Artifacts, ScratchFiles: snap.ScratchFiles, Summaries: snap.Summaries, Usage: snap.Usage, CreatedAt: snap.CreatedAt, UpdatedAt: snap.UpdatedAt}
	s.Previews, s.Knowledge, s.KnowledgeFacts = snap.Previews, snap.Knowledge, snap.KnowledgeFacts
	s.StagedManifests = snap.StagedManifests
	s.LastError, s.LastStopped = snap.LastError, snap.LastStopped
	s.LLMModel = snap.LLMModel
	s.WorkspaceID = snap.WorkspaceID
	s.PendingInput = snap.PendingInput
	if snap.Running {
		s.LastStopped = true
	} // A saved active run was interrupted by process exit.
	s.ParentSessionID, s.DeletedAt = snap.ParentSessionID, snap.DeletedAt
	s.ArchivedMessages = snap.ArchivedMessages
	s.Sources = snap.Sources
	for _, messages := range [][]llm.Message{s.Messages, s.ArchivedMessages} {
		for index := range messages {
			for call := range messages[index].ToolCalls {
				item := &messages[index].ToolCalls[call]
				item.ArgumentsError = llm.ToolArgumentsError(json.RawMessage(item.Function.Arguments))
			}
		}
	}
	s.RestoreSources()
	if s.Mode == "" {
		s.Mode = contracts.ModeReadonly
	}
	for _, x := range sk {
		if x.Name == snap.ActiveSkill {
			s.ActiveSkill = x
		}
	}
	return s, nil
}

func List(storage string) ([]Meta, error) {
	return list(storage, false)
}

func ListDeleted(storage string) ([]Meta, error) { return list(storage, true) }

func list(storage string, deleted bool) ([]Meta, error) {
	entries, err := os.ReadDir(dir(storage))
	if os.IsNotExist(err) {
		return []Meta{}, nil
	}
	if err != nil {
		return nil, err
	}
	out := []Meta{}
	for _, e := range entries {
		if e.IsDir() || filepath.Ext(e.Name()) != ".json" || strings.HasSuffix(e.Name(), ".meta.json") {
			continue
		}
		id := strings.TrimSuffix(e.Name(), ".json")
		b, err := os.ReadFile(filepath.Join(dir(storage), id+".meta.json"))
		var meta Meta
		if err == nil && json.Unmarshal(b, &meta) == nil && meta.ID == id && meta.Version >= 2 {
			if (meta.DeletedAt != nil) == deleted {
				out = append(out, meta)
			}
			continue
		}
		b, err = os.ReadFile(filepath.Join(dir(storage), e.Name()))
		if err != nil {
			continue
		}
		var snap Snapshot
		if json.Unmarshal(b, &snap) != nil || snap.ID == "" {
			continue
		}
		if (snap.DeletedAt != nil) != deleted {
			continue
		}
		meta = Meta{Version: 2, WorkspaceID: snap.WorkspaceID, ID: snap.ID, Title: snap.Title, UpdatedAt: snap.UpdatedAt, NManifests: len(snap.Manifests), NMessages: len(snap.Messages), NArtifacts: len(snap.Artifacts), ParentSessionID: snap.ParentSessionID, DeletedAt: snap.DeletedAt}
		out = append(out, meta)
		indexed, _ := json.Marshal(meta)
		if err := os.WriteFile(filepath.Join(dir(storage), id+".meta.json"), indexed, 0o644); err != nil {
			return nil, err
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].UpdatedAt.After(out[j].UpdatedAt) })
	return out, nil
}
