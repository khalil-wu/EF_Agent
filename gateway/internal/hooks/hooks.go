// Package hooks holds the EEG-specific behaviour attached to the loop. The loop knows nothing about EEG.
package hooks

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/artifacts"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/skills"
)

type llmMessages = llm.Message

const maxToolResultChars = 12000 // ≈ 4k tokens; larger results are summarized and kept on disk

// Retrieval is what the knowledge layer returns for a query: text chunks plus graph facts and linked entities.
type Retrieval struct {
	Chunks   []json.RawMessage `json:"chunks"`
	Facts    []json.RawMessage `json:"facts"`
	Entities []json.RawMessage `json:"entities"`
	Path     []json.RawMessage `json:"path,omitempty"`
}

// Retriever fetches knowledge for a query (wired to the worker's search_knowledge tool). May be nil.
type Retriever func(ctx context.Context, query string, k int, sessionID string) (*Retrieval, error)

// Build returns the hook set: knowledge retrieval, visibility + permission + requires gating, result
// truncation, and manifest/artifact capture.
func Build(all map[string]contracts.ToolDefinition, storageDir string, retrieve Retriever) agent.Hooks {
	return agent.Hooks{
		PrepareRequest: func(ctx context.Context, s *agent.Session, _ *string) error {
			if retrieve == nil {
				return nil
			}
			last := lastUserText(s.Messages)
			if last == "" || !needsKnowledge(last) {
				s.Knowledge, s.KnowledgeFacts, s.KnowledgeQuery = nil, nil, ""
				s.KnowledgeUpdated = false
				return nil
			}
			q := last
			if s.ActiveSkill != nil {
				q = s.ActiveSkill.Name + " " + q
			}
			if q == s.KnowledgeQuery {
				return nil
			}
			res, err := retrieve(ctx, q, 3, s.ID)
			if err != nil || res == nil {
				s.Knowledge, s.KnowledgeFacts, s.KnowledgeQuery = nil, nil, ""
				s.KnowledgeUpdated = false
				return nil
			}
			if len(res.Chunks) == 0 && len(res.Facts) == 0 {
				s.Knowledge, s.KnowledgeFacts, s.KnowledgeQuery = nil, nil, ""
				s.KnowledgeUpdated = false
				return nil
			}
			body, _ := json.Marshal(res)
			_ = json.Unmarshal(budgetRetrieval(body, maxToolResultChars), res)
			s.KnowledgeQuery = q
			s.Knowledge = res.Chunks
			s.RememberSources(res.Chunks)
			s.KnowledgeFacts = res.Facts
			s.KnowledgeUpdated = true
			return nil
		},

		BeforeTool: func(_ context.Context, s *agent.Session, c agent.ToolCall) (bool, string) {
			if c.Name == "read_skill" || c.Name == "request_user_input" {
				return false, ""
			}
			def, ok := all[c.Name]
			if !ok {
				return true, "工具未登记：" + c.Name
			}
			visible := false
			for _, n := range skills.VisibleTools(s.ActiveSkill) {
				if n == c.Name {
					visible = true
					break
				}
			}
			if !visible {
				return true, fmt.Sprintf("工具 %s 在当前 skill 下不可见；先用 read_skill 激活包含它的 skill", c.Name)
			}
			if contracts.PermissionMode(def.PermissionModeMin).Rank() > s.Mode.Rank() {
				return true, fmt.Sprintf("当前模式 %s 不允许 %s（需要 %s）；请告知用户切换模式", s.Mode, c.Name, def.PermissionModeMin)
			}
			if def.Requires == nil {
				return false, ""
			}
			var args map[string]any
			_ = json.Unmarshal(c.Args, &args)
			str := func(k string) string {
				if v, ok := args[k].(string); ok {
					return v
				}
				return ""
			}
			for argName, wantKind := range def.Requires.ArtifactKind {
				id := str(argName)
				if id == "" {
					return true, fmt.Sprintf("参数 %s 缺失，需要一个 %s 产物 id", argName, wantKind)
				}
				rec, err := artifacts.Load(storageDir, id)
				if err != nil {
					return true, fmt.Sprintf("参数 %s=%s 不是已登记的产物；只能引用工具结果或 runtime_context 里出现过的 artifact_id", argName, id)
				}
				if rec.Kind != wantKind {
					return true, fmt.Sprintf("参数 %s 需要 kind=%s 的产物，%s 是 kind=%s；请先用 %s 生成正确的产物", argName, wantKind, id, rec.Kind, producerFor(wantKind))
				}
			}
			for argName, steps := range def.Requires.ProvenanceSteps {
				id := str(argName)
				if !strings.HasPrefix(id, "art_") {
					return true, fmt.Sprintf("参数 %s 需要包含 %s 步骤的处理产物", argName, strings.Join(steps, ", "))
				}
				rec, err := artifacts.Load(storageDir, id)
				if err != nil {
					return true, fmt.Sprintf("无法核对 %s 的处理链：%v", id, err)
				}
				for _, st := range steps {
					if !rec.HasStep(st) {
						return true, fmt.Sprintf("前置条件不满足：%s 的处理链里没有 %s。先用 preprocess 做 %s，再调用 %s", id, st, st, c.Name)
					}
				}
			}
			if len(def.Requires.ManifestFieldsKnown) > 0 {
				mid := str("manifest_id")
				if mid == "" {
					mid = str("source_id")
				}
				for _, m := range s.Manifests {
					if m.ManifestID != mid {
						continue
					}
					for _, f := range def.Requires.ManifestFieldsKnown {
						if f == "channel_layout" && (m.ChannelLayout == nil || m.ChannelLayout.Source == "unknown") {
							return true, "前置条件不满足：通道名未知。先用 device_lookup 或向用户询问通道布局"
						}
						if f == "unit" && (m.Unit == nil || m.Unit.Source == "unknown") {
							return true, "前置条件不满足：单位未知。先向用户确认单位"
						}
					}
				}
			}
			return false, ""
		},

		AfterTool: func(_ context.Context, s *agent.Session, c agent.ToolCall, result json.RawMessage, isErr bool) (json.RawMessage, bool) {
			isKnowledge := c.Name == "search_knowledge" || c.Name == "knowledge_graph"
			if !isErr && isKnowledge {
				result = budgetRetrieval(result, maxToolResultChars)
			}
			// A failed script can still return files and records completed before its error or cancellation.
			if !isErr || c.Name == "run_python" {
				if c.Name == "workspace_files" {
					var relocated struct {
						Moves []contracts.FileMove `json:"relocations"`
					}
					json.Unmarshal(result, &relocated)
					contracts.UpdatePaths(s.Manifests, relocated.Moves)
				}
				if isKnowledge || c.Name == "read_document" {
					var evidence Retrieval
					if json.Unmarshal(result, &evidence) == nil {
						s.RememberSources(evidence.Chunks)
					}
				}
				if c.Name == "check_model_compat" {
					var a struct {
						ManifestID string `json:"manifest_id"`
					}
					var out struct {
						Compat []contracts.CompatResult `json:"compat"`
					}
					_ = json.Unmarshal(c.Args, &a)
					if json.Unmarshal(result, &out) == nil {
						for _, m := range s.Manifests {
							if m.ManifestID == a.ManifestID {
								m.Compat = out.Compat
							}
						}
					}
				}
				var out struct {
					Manifest  *contracts.Manifest   `json:"manifest"`
					Manifests []*contracts.Manifest `json:"manifests"`
					Artifact  json.RawMessage       `json:"artifact"`
					Artifacts []json.RawMessage     `json:"artifacts"`
					Files     []string              `json:"files"`
				}
				if json.Unmarshal(result, &out) == nil {
					if out.Manifest != nil {
						out.Manifests = append(out.Manifests, out.Manifest)
					}
					for _, manifest := range out.Manifests {
						replaced := false
						for i, m := range s.Manifests {
							if m.SameRecord(manifest) {
								manifest.OriginalName = m.OriginalName
								manifest.UploadGroupID = m.UploadGroupID
								s.Manifests[i] = manifest
								replaced = true
							}
						}
						if !replaced {
							s.Manifests = append(s.Manifests, manifest)
						}
					}
					if len(out.Artifact) > 2 {
						addArtifact(s, out.Artifact)
					}
					for _, a := range out.Artifacts {
						if len(a) > 2 {
							addArtifact(s, a)
						}
					}
					for _, f := range out.Files {
						addScratch(s, f)
					}
				}
			}
			if c.Name == "recognize_emotion" {
				// Updated facts already live in latest_context; don't send their full duplicate records.
				var fields map[string]json.RawMessage
				_ = json.Unmarshal(result, &fields)
				delete(fields, "manifests")
				result, _ = json.Marshal(fields)
			}
			if c.Name == "analyze_signal" && !isErr {
				var fields map[string]json.RawMessage
				_ = json.Unmarshal(result, &fields)
				// Source records and artifacts are already in latest_context; keep the measured summary and result links here.
				delete(fields, "manifest")
				delete(fields, "artifacts")
				result, _ = json.Marshal(fields)
			}
			// read_document already limits the returned text; its duplicate citation view is intentional.
			if len(result) > maxToolResultChars && c.Name != "read_document" && !isKnowledge {
				return budgetToolResult(result, maxToolResultChars), isErr
			}
			return result, isErr
		},
	}
}

// VisibilityOnly is the "no_requires" ablation: keeps visibility and permission gating, drops precondition checks.
func VisibilityOnly(all map[string]contracts.ToolDefinition) func(context.Context, *agent.Session, agent.ToolCall) (bool, string) {
	stripped := map[string]contracts.ToolDefinition{}
	for k, v := range all {
		v.Requires = nil
		stripped[k] = v
	}
	return Build(stripped, "", nil).BeforeTool
}

func addArtifact(s *agent.Session, raw json.RawMessage) {
	var idOnly struct {
		ID string `json:"artifact_id"`
	}
	_ = json.Unmarshal(raw, &idOnly)
	if idOnly.ID == "" {
		return
	}
	for _, a := range s.Artifacts {
		var e struct {
			ID string `json:"artifact_id"`
		}
		if json.Unmarshal(a, &e) == nil && e.ID == idOnly.ID {
			return
		}
	}
	s.Artifacts = append(s.Artifacts, raw)
}

func addScratch(s *agent.Session, f string) {
	for _, x := range s.ScratchFiles {
		if x == f {
			return
		}
	}
	s.ScratchFiles = append(s.ScratchFiles, f)
}

func lastUserText(msgs []llmMessages) string {
	for i := len(msgs) - 1; i >= 0; i-- {
		m := msgs[i]
		if m.Role != "user" {
			continue
		}
		t := strings.TrimSpace(m.Content)
		if t == "" || strings.HasPrefix(t, "<") {
			continue
		}
		return t
	}
	return ""
}

// Automatic retrieval serves method and evidence questions. The model can still call
// search_knowledge explicitly when a practical analysis needs a specific source.
func needsKnowledge(text string) bool {
	methodQuestion := mentionsAny(text, "什么是", "是什么", "是什么意思", "为什么", "为何", "原理", "依据", "证据", "文献", "论文", "定义", "规范", "区别", "怎么解释", "意味着", "可靠性", "泛化", "适合", "怎么选", "如何选择", "合理", "阈值")
	eegTopic := mentionsAny(text, "EEG", "脑电", "alpha", "beta", "delta", "theta", "gamma", "α", "β", "δ", "θ", "γ", "伪迹", "ICA", "ASR", "ICLabel", "RANSAC", "PREP", "ERP", "ERSP", "wPLI", "PLV", "PPC", "FOOOF", "specparam", "Hjorth", "Welch", "PSD", "差分熵", "连接性", "情绪", "通道", "采样", "电极", "滤波", "功率", "节律", "CAMER", "EEGNet", "LaBraM", "NeuroLM", "BIOT", "DEAP", "AMIGOS", "MAHNOB", "EAV", "Emotiv", "OpenBCI")
	capabilityQuestion := mentionsAny(text, "能不能", "能否", "可以用", "能做", "可用于") &&
		mentionsAny(text, "CAMER", "EEGPT", "EEGNet", "LaBraM", "NeuroLM", "BIOT")
	return eegTopic && (methodQuestion || capabilityQuestion)
}

func producerFor(kind string) string {
	switch kind {
	case "windows":
		return "segment_signal"
	case "features":
		return "extract_features"
	case "normalized_signal":
		return "preprocess"
	case "emotion_assessment":
		return "save_emotion_assessment"
	default:
		return "对应的工具"
	}
}

func mentionsAny(s string, words ...string) bool {
	l := strings.ToLower(s)
	for _, w := range words {
		if strings.Contains(l, strings.ToLower(w)) {
			return true
		}
	}
	return false
}
