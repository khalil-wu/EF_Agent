package hooks

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/skills"
)

func writeArtifact(t *testing.T, dir, id, kind string, steps []string) {
	t.Helper()
	rec := map[string]any{"artifact_id": id, "kind": kind, "uri": "x", "source_manifest_ids": []string{"sig_1"},
		"provenance": map[string]any{"created_by": "t@1", "created_at": "now", "params": map[string]any{}, "steps": steps}}
	b, _ := json.Marshal(rec)
	if err := os.WriteFile(filepath.Join(dir, "artifacts", id+".json"), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestRequiresArtifactKindAndSteps(t *testing.T) {
	dir := t.TempDir()
	_ = os.MkdirAll(filepath.Join(dir, "artifacts"), 0o755)
	writeArtifact(t, dir, "art_aaaaaa", "windows", []string{"unit_uV", "bandpass_0.5_50", "segment_4_2"})
	writeArtifact(t, dir, "art_bbbbbb", "features", []string{"unit_uV"})
	writeArtifact(t, dir, "art_cccccc", "windows", []string{"unit_uV", "segment_4_2"})

	all := map[string]contracts.ToolDefinition{
		"extract_features": {Name: "extract_features", PermissionModeMin: "readonly",
			Requires: &contracts.ToolRequires{ArtifactKind: map[string]string{"windows_id": "windows"}, ProvenanceSteps: map[string][]string{"windows_id": {"bandpass"}}}},
	}
	h := Build(all, dir, nil)
	s := &agent.Session{Mode: contracts.ModeReadonly, ActiveSkill: &skills.Skill{Name: "x", AllowedTools: []string{"extract_features"}}}
	call := func(id string) (bool, string) {
		return h.BeforeTool(context.Background(), s, agent.ToolCall{Name: "extract_features", Args: json.RawMessage(`{"windows_id":"` + id + `"}`)})
	}
	if blocked, why := call("art_aaaaaa"); blocked {
		t.Fatalf("good artifact blocked: %s", why)
	}
	if blocked, why := call("art_bbbbbb"); !blocked || !strings.Contains(why, "kind=windows") {
		t.Fatalf("wrong kind not blocked: %v %s", blocked, why)
	}
	if blocked, why := call("art_cccccc"); !blocked || !strings.Contains(why, "bandpass") {
		t.Fatalf("missing step not blocked: %v %s", blocked, why)
	}
	if blocked, why := call("../../etc"); !blocked || !strings.Contains(why, "不是已登记") {
		t.Fatalf("bad id not blocked: %v %s", blocked, why)
	}
}

func TestVisibilityAndPermission(t *testing.T) {
	all := map[string]contracts.ToolDefinition{
		"preprocess": {Name: "preprocess", PermissionModeMin: "readonly"},
		"run_model":  {Name: "run_model", PermissionModeMin: "readonly"},
		"gpu_only":   {Name: "gpu_only", PermissionModeMin: "gpu"},
		"run_python": {Name: "run_python", PermissionModeMin: "readonly"},
	}
	h := Build(all, t.TempDir(), nil)
	s := &agent.Session{Mode: contracts.ModeReadonly}
	if blocked, why := h.BeforeTool(context.Background(), s, agent.ToolCall{Name: "preprocess", Args: json.RawMessage(`{}`)}); blocked {
		t.Fatalf("shared preprocess tool should be visible without a skill: %s", why)
	}
	if blocked, why := h.BeforeTool(context.Background(), s, agent.ToolCall{Name: "run_model", Args: json.RawMessage(`{}`)}); blocked {
		t.Fatalf("registered inference should be base-visible in readonly: %s", why)
	}
	if blocked, why := h.BeforeTool(context.Background(), s, agent.ToolCall{Name: "run_python", Args: json.RawMessage(`{}`)}); blocked {
		t.Fatalf("run_python is a base tool and must be visible: %s", why)
	}
	s.ActiveSkill = &skills.Skill{Name: "x", AllowedTools: []string{"gpu_only"}}
	if blocked, _ := h.BeforeTool(context.Background(), s, agent.ToolCall{Name: "preprocess", Args: json.RawMessage(`{}`)}); blocked {
		t.Fatal("preprocess should be visible with skill")
	}
	if blocked, why := h.BeforeTool(context.Background(), s, agent.ToolCall{Name: "gpu_only", Args: json.RawMessage(`{}`)}); !blocked || !strings.Contains(why, "gpu") {
		t.Fatalf("gpu tool should be blocked in readonly: %v %s", blocked, why)
	}
}

func TestAfterToolCapturesArtifactsAndFiles(t *testing.T) {
	h := Build(map[string]contracts.ToolDefinition{}, t.TempDir(), nil)
	s := &agent.Session{}
	one := json.RawMessage(`{"status":"ok","artifact":{"artifact_id":"art_123456","kind":"plot","uri":"p.png","source_manifest_ids":["sig_1"],"summary":{"kind":"raw","channel_names":["a","b"]},"provenance":{"created_by":"plot@1.0","created_at":"t","params":{},"steps":["plot_raw"]}}}`)
	h.AfterTool(context.Background(), s, agent.ToolCall{Name: "plot"}, one, false)
	h.AfterTool(context.Background(), s, agent.ToolCall{Name: "plot"}, one, false)
	if len(s.Artifacts) != 1 {
		t.Fatalf("expected 1 artifact, got %d", len(s.Artifacts))
	}
	if !strings.Contains(string(s.Artifacts[0]), "channel_names") {
		t.Fatalf("compact view should preserve scientific channel names: %s", s.Artifacts[0])
	}
	many := json.RawMessage(`{"status":"ok","stdout":"x","files":["run_001.out","notes.txt"],"artifacts":[{"artifact_id":"art_aaaaaa","kind":"table","uri":"t.csv","source_manifest_ids":[],"provenance":{"created_by":"run_python@1.0","created_at":"t","params":{},"steps":["script"]}},{"artifact_id":"art_bbbbbb","kind":"report","uri":"r.md","source_manifest_ids":[],"provenance":{"created_by":"run_python@1.0","created_at":"t","params":{},"steps":["script"]}}]}`)
	h.AfterTool(context.Background(), s, agent.ToolCall{Name: "run_python"}, many, false)
	if len(s.Artifacts) != 3 {
		t.Fatalf("expected 3 artifacts, got %d", len(s.Artifacts))
	}
	if len(s.ScratchFiles) != 2 || s.ScratchFiles[1] != "notes.txt" {
		t.Fatalf("scratch files not captured: %v", s.ScratchFiles)
	}
}

func TestAnySuccessfulToolCanRegisterAndUpdateSourceRecords(t *testing.T) {
	hook := Build(nil, t.TempDir(), nil)
	session := &agent.Session{}
	initial := json.RawMessage(`{"status":"ok","manifest":{"manifest_id":"sig_script","modality":"eeg","source_path":"/data/a.npz","attachment_sha256":"abc","original_name":"original.npz","upload_group_id":"upload_a","n_channels":2},"artifacts":[{"artifact_id":"art_script","kind":"report","uri":"report.md"}]}`)
	hook.AfterTool(context.Background(), session, agent.ToolCall{Name: "run_python"}, initial, false)
	if len(session.Manifests) != 1 || len(session.Artifacts) != 1 {
		t.Fatalf("a script's source records were not registered: %+v", session)
	}
	updated := json.RawMessage(`{"status":"ok","manifests":[{"manifest_id":"sig_script","modality":"eeg","source_path":"/data/a.npz","attachment_sha256":"abc","n_channels":3},{"manifest_id":"sig_second","modality":"eeg","source_path":"/data/b.npz","attachment_sha256":"def","n_channels":4}]}`)
	hook.AfterTool(context.Background(), session, agent.ToolCall{Name: "additional_reader"}, updated, false)
	if len(session.Manifests) != 2 || session.Manifests[0].NChannels != 3 || session.Manifests[0].OriginalName != "original.npz" || session.Manifests[0].UploadGroupID != "upload_a" {
		t.Fatalf("generic capture lost identity or duplicated the source: %+v", session.Manifests)
	}
	hook.AfterTool(context.Background(), session, agent.ToolCall{Name: "failed_reader"}, json.RawMessage(`{"manifest":{"manifest_id":"sig_failed"}}`), true)
	if len(session.Manifests) != 2 {
		t.Fatal("a failed tool registered source records")
	}
}

func TestPrepareRequestRetrievesOncePerQuery(t *testing.T) {
	calls := 0
	retr := func(_ context.Context, q string, k int, _ string) (*Retrieval, error) {
		calls++
		return &Retrieval{
			Chunks: []json.RawMessage{json.RawMessage(`{"chunk_id":"k_11111111","doc_id":"d","title":"t","section":"s","text":"x"}`)},
			Facts:  []json.RawMessage{json.RawMessage(`{"fact_id":"r_1","text":"A —rel→ B","evidence":["k_11111111"]}`)},
		}, nil
	}
	h := Build(map[string]contracts.ToolDefinition{}, t.TempDir(), retr)
	s := &agent.Session{Messages: []llm.Message{{Role: "user", Content: "差分熵是什么"}}}
	sys := "SYS"
	_ = h.PrepareRequest(context.Background(), s, &sys)
	_ = h.PrepareRequest(context.Background(), s, &sys)
	if calls != 1 || len(s.Knowledge) != 1 || len(s.KnowledgeFacts) != 1 || !s.KnowledgeUpdated {
		t.Fatalf("calls=%d knowledge=%d facts=%d updated=%v", calls, len(s.Knowledge), len(s.KnowledgeFacts), s.KnowledgeUpdated)
	}
	s.ActiveSkill = &skills.Skill{Name: "feature_extraction"}
	_ = h.PrepareRequest(context.Background(), s, &sys)
	if calls != 2 {
		t.Fatalf("skill change should re-query, calls=%d", calls)
	}
}

func TestMethodQuestionsIncludeTheRegisteredScientificMethods(t *testing.T) {
	for _, question := range []string{"ASR 的原理是什么？", "wPLI 与 PLV 的区别是什么？", "ERP 的基线依据是什么？", "Hjorth mobility 怎么解释？", "FOOOF 参数怎么选？"} {
		if !needsKnowledge(question) {
			t.Errorf("professional method question skipped retrieval: %s", question)
		}
	}
	for _, request := range []string{"你好", "检查一下这个文件", "用 ASR 清理这个 trial", "保存 CSV"} {
		if needsKnowledge(request) {
			t.Errorf("routine execution triggered generic retrieval: %s", request)
		}
	}
}

func TestLongRetrievalKeepsSourceAndPageInsteadOfDiscardingChunks(t *testing.T) {
	rows := []map[string]any{}
	for _, id := range []string{"k_111111", "k_222222", "k_333333"} {
		rows = append(rows, map[string]any{"chunk_id": id, "text": strings.Repeat("脑电", 1500), "page": 2, "source": "研究说明.pdf · 第 2 页"})
	}
	body, _ := json.Marshal(map[string]any{"status": "ok", "chunks": rows})
	hook := Build(nil, t.TempDir(), nil)
	result, failed := hook.AfterTool(context.Background(), &agent.Session{}, agent.ToolCall{Name: "search_knowledge"}, body, false)
	var parsed struct {
		Chunks []struct {
			ID     string `json:"chunk_id"`
			Page   int    `json:"page"`
			Source string `json:"source"`
		} `json:"chunks"`
	}
	if json.Unmarshal(result, &parsed) != nil || failed || len(parsed.Chunks) == 0 || len(result) > maxToolResultChars {
		t.Fatal("retrieval evidence lost", len(result), failed)
	}
	if parsed.Chunks[0].Page != 2 || parsed.Chunks[0].ID != "k_111111" || parsed.Chunks[0].Source == "" {
		t.Fatal(parsed.Chunks[0])
	}
}

func TestAutomaticKnowledgeSkipsExecutionRequestsAndClearsStaleEvidence(t *testing.T) {
	calls := 0
	retr := func(_ context.Context, _ string, _ int, _ string) (*Retrieval, error) {
		calls++
		return &Retrieval{Chunks: []json.RawMessage{json.RawMessage(`{"chunk_id":"k_11111111"}`)}}, nil
	}
	h := Build(nil, t.TempDir(), retr)
	s := &agent.Session{Messages: []llm.Message{{Role: "user", Content: "差分熵是什么？"}}}
	system := "SYS"
	if err := h.PrepareRequest(context.Background(), s, &system); err != nil || calls != 1 || len(s.Knowledge) != 1 {
		t.Fatal(calls, err)
	}
	s.Messages = append(s.Messages, llm.Message{Role: "user", Content: "请画出上传信号的前两秒波形"})
	if err := h.PrepareRequest(context.Background(), s, &system); err != nil || calls != 1 || len(s.Knowledge) != 0 || len(s.KnowledgeFacts) != 0 {
		t.Fatalf("unrelated evidence leaked into execution request: %d %v", calls, s.Knowledge)
	}
	if needsKnowledge("连接检查，请只回复 OK") || needsKnowledge("请预处理并画功率谱") {
		t.Fatal("simple execution requests must not trigger retrieval")
	}
	if needsKnowledge("这段 EEG 能否用于情绪识别？") || !needsKnowledge("为什么 alpha 峰会移动？") {
		t.Fatal("data feasibility uses record/model contracts; interpretation questions need evidence")
	}
	if needsKnowledge("为什么今天下雨？") || needsKnowledge("我需要一份论文格式的文档") {
		t.Fatal("unrelated topics must not inject EEG evidence")
	}
	for _, query := range []string{"能不能帮我分析这个数据", "能不能帮我分析这段脑电", "为什么这个文件传不上去", "这是什么数据"} {
		if needsKnowledge(query) {
			t.Fatalf("ordinary execution request triggered retrieval: %s", query)
		}
	}
	if !needsKnowledge("Emotiv EPOC 能不能喂给 CAMER？") {
		t.Fatal("model applicability question lost evidence retrieval")
	}
}

func TestCompletedTaskDoesNotForceFailureDisclaimer(t *testing.T) {
	h := Build(nil, t.TempDir(), nil)
	if h.FinishTurn != nil {
		t.Fatal("the EEG hooks must not force an extra disclaimer turn after recovered tool errors")
	}
}

func TestReadDocumentAndSearchCapturePersistentCitationExcerpts(t *testing.T) {
	h := Build(nil, t.TempDir(), nil)
	s := &agent.Session{}
	read := json.RawMessage(`{"status":"ok","pages":[{"page":2,"text":"observed paragraph"}],"chunks":[{"chunk_id":"k_abcdef123456","text":"observed paragraph","page":2,"start_offset":2000,"source_kind":"user_upload"}]}`)
	h.AfterTool(context.Background(), s, agent.ToolCall{Name: "read_document"}, read, false)
	search := json.RawMessage(`{"chunks":[{"chunk_id":"k_abcdef123456","text":"new content"},{"chunk_id":"k_123456abcdef","text":"second excerpt"}]}`)
	h.AfterTool(context.Background(), s, agent.ToolCall{Name: "search_knowledge"}, search, false)
	if len(s.Sources) != 2 || !strings.Contains(string(s.Sources["k_abcdef123456"]), "observed paragraph") || !strings.Contains(string(s.Sources["k_abcdef123456"]), `"start_offset":2000`) {
		t.Fatalf("read context lost or replaced: %v", s.Sources)
	}
	failed := json.RawMessage(`{"chunks":[{"chunk_id":"k_999999","text":"failed read"}]}`)
	h.AfterTool(context.Background(), s, agent.ToolCall{Name: "read_document"}, failed, true)
	if len(s.Sources) != 2 {
		t.Fatal("failed read created evidence")
	}
}
