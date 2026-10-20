package agent

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/skills"
)

// fakeLLM answers a scripted sequence of SSE responses.
func fakeLLM(t *testing.T, scripts []string) *llm.Client {
	i := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if i >= len(scripts) {
			t.Fatalf("unexpected extra LLM request %d", i)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte(scripts[i]))
		i++
	}))
	t.Cleanup(srv.Close)
	return llm.New(srv.URL, "", "fake")
}

type fakeExec struct {
	calls []string
	args  []string
}

type responseExec struct{ body json.RawMessage }

func (f responseExec) Call(context.Context, string, json.RawMessage) (json.RawMessage, error) {
	return f.body, nil
}

func TestToolPresentationRetainsMeasurementsWhileModelReceivesBriefResult(t *testing.T) {
	body := json.RawMessage(`{"status":"ok","manifest":{"manifest_id":"sig_123456"},"summary":{"n_windows":4},"artifacts":[{"artifact_id":"art_123456","kind":"features","summary":{"relative_bandpower":{"alpha":0.9}}}]}`)
	brief := json.RawMessage(`{"status":"ok","summary":{"n_windows":4}}`)
	cfg := Config{Executor: responseExec{body}, AllTools: map[string]contracts.ToolDefinition{"analyze_signal": {Name: "analyze_signal"}}, Hooks: Hooks{AfterTool: func(context.Context, *Session, ToolCall, json.RawMessage, bool) (json.RawMessage, bool) {
		return brief, false
	}}}
	var events []Event
	result, failed := executeOne(context.Background(), cfg, &Session{}, ToolCall{ID: "c1", Name: "analyze_signal", Args: json.RawMessage(`{}`)}, func(e Event) { events = append(events, e) })
	if failed || string(result) != string(brief) {
		t.Fatalf("model result changed: %s", result)
	}
	if len(events) != 3 || string(events[1].Result) != string(body) || events[2].Type != "artifact_ready" || !strings.Contains(string(events[2].Result), `"alpha":0.9`) {
		t.Fatalf("UI lost actual measurements: %+v", events)
	}
}

// contractsToolDef is a local alias so tests read naturally.
type contractsToolDef = contracts.ToolDefinition

func (f *fakeExec) Call(_ context.Context, name string, args json.RawMessage) (json.RawMessage, error) {
	f.calls = append(f.calls, name)
	f.args = append(f.args, string(args))
	return json.RawMessage(`{"status":"ok","manifest":{"manifest_id":"sig_1","modality":"eeg","attachment_sha256":"abc","n_channels":8,"compat":[]}}`), nil
}

func sse(chunks ...string) string {
	var b strings.Builder
	for _, c := range chunks {
		b.WriteString("data: " + c + "\n\n")
	}
	b.WriteString("data: [DONE]\n\n")
	return b.String()
}

func TestWorkspaceToolsFollowTheConversationWorkspace(t *testing.T) {
	cfg := Config{AllTools: map[string]contracts.ToolDefinition{}}
	for _, name := range []string{"run_python", "write_file", "edit_file", "save_to_workspace", "workspace_files", "workspace_quality"} {
		cfg.AllTools[name] = contracts.ToolDefinition{Name: name, InputSchema: json.RawMessage(`{"type":"object"}`)}
	}
	for _, workspace := range []string{"", "ws_attached"} {
		specs := visibleToolSpecs(cfg, &Session{WorkspaceID: workspace})
		names := map[string]bool{}
		for _, spec := range specs {
			names[spec.Function.Name] = true
		}
		if !names["run_python"] {
			t.Fatal("uploaded-file analysis must remain available")
		}
		for _, name := range []string{"write_file", "edit_file", "save_to_workspace", "workspace_files", "workspace_quality"} {
			if names[name] != (workspace != "") {
				t.Fatalf("workspace=%q tools=%v", workspace, names)
			}
		}
	}
}

func TestAgentEndTimestampFollowsCompletedOutput(t *testing.T) {
	c := fakeLLM(t, []string{sse(`{"choices":[{"delta":{"content":"完成"}}]}`)})
	cfg := Config{LLM: c, SystemFn: func(*Session) string { return "system" }, FileCtxFn: func(*Session) string { return "context" }}
	var events []Event
	_, err := Run(context.Background(), cfg, &Session{}, "hello", func(event Event) { events = append(events, event) })
	if err != nil {
		t.Fatal(err)
	}
	end := events[len(events)-1]
	if end.Type != "agent_end" {
		t.Fatal(end.Type)
	}
	for _, event := range events {
		if end.Timestamp.Before(event.Timestamp) {
			t.Fatalf("end timestamp predates %s", event.Type)
		}
	}
}

func TestLoopRunsToolThenAnswers(t *testing.T) {
	c := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"inspect_record","arguments":"{\"path\":"}}]}}]}`,
			`{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"x.npy\"}"}}]}}]}`),
		sse(`{"choices":[{"delta":{"content":"文件有 8 个通道。"}}]}`),
	})
	ex := &fakeExec{}
	all := map[string]contracts.ToolDefinition{"inspect_record": {Name: "inspect_record", Description: "d", InputSchema: json.RawMessage(`{"type":"object"}`), PermissionModeMin: "readonly"}}
	var events []Event
	cfg := Config{LLM: c, Executor: ex, AllTools: all,
		SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" },
		Hooks: Hooks{AfterTool: func(_ context.Context, s *Session, c ToolCall, r json.RawMessage, e bool) (json.RawMessage, bool) {
			var out struct {
				Manifest *contracts.Manifest `json:"manifest"`
			}
			if json.Unmarshal(r, &out) == nil && out.Manifest != nil {
				s.Manifests = append(s.Manifests, out.Manifest)
			}
			return r, e
		}}}
	s := &Session{ID: "t", Mode: contracts.ModeReadonly}
	final, err := Run(context.Background(), cfg, s, "读一下这个文件", func(e Event) { events = append(events, e) })
	if err != nil {
		t.Fatal(err)
	}
	if final != "文件有 8 个通道。" {
		t.Fatalf("final=%q", final)
	}
	if len(ex.calls) != 1 || ex.calls[0] != "inspect_record" {
		t.Fatalf("calls=%v", ex.calls)
	}
	if !strings.Contains(ex.args[0], `"_session_id":"t"`) || !strings.Contains(ex.args[0], `"path":"x.npy"`) {
		t.Fatalf("session id not injected or args lost: %s", ex.args[0])
	}
	if len(s.Manifests) != 1 {
		t.Fatalf("manifest not captured")
	}
	// transcript: user, assistant(tool_calls), tool, assistant
	if len(s.Messages) != 4 || s.Messages[2].Role != "tool" || s.Messages[2].ToolCallID != "c1" {
		t.Fatalf("bad transcript: %+v", s.Messages)
	}
	types := map[string]int{}
	for _, e := range events {
		types[e.Type]++
	}
	for _, want := range []string{"agent_start", "turn_start", "tool_execution_start", "tool_execution_end", "message_delta", "turn_end", "agent_end"} {
		if types[want] == 0 {
			t.Errorf("missing event %s", want)
		}
	}
}

func TestBeforeToolBlockReachesModel(t *testing.T) {
	c := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"run_model","arguments":"{}"}}]}}]}`),
		sse(`{"choices":[{"delta":{"content":"该模型不可用。"}}]}`),
	})
	ex := &fakeExec{}
	all := map[string]contracts.ToolDefinition{"run_model": {Name: "run_model", PermissionModeMin: "gpu"}}
	cfg := Config{LLM: c, Executor: ex, AllTools: all,
		SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" },
		Hooks: Hooks{BeforeTool: func(context.Context, *Session, ToolCall) (bool, string) { return true, "需要 gpu 模式" }}}
	s := &Session{ID: "t", Mode: contracts.ModeReadonly}
	if _, err := Run(context.Background(), cfg, s, "跑模型", func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if len(ex.calls) != 0 {
		t.Fatalf("blocked tool was executed")
	}
	if !strings.Contains(s.Messages[2].Content, "需要 gpu 模式") {
		t.Fatalf("block reason not in tool result: %s", s.Messages[2].Content)
	}
}

func TestFinishTurnInjectsOneMoreRound(t *testing.T) {
	c := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"content":"完成。"}}]}`),
		sse(`{"choices":[{"delta":{"content":"补充：不能做 X。"}}]}`),
	})
	n := 0
	cfg := Config{LLM: c, Executor: &fakeExec{}, AllTools: map[string]contracts.ToolDefinition{},
		SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" },
		Hooks: Hooks{FinishTurn: func(_ context.Context, _ *Session, ans string) (bool, string) {
			n++
			if n == 1 {
				return true, "请补充限制说明"
			}
			return false, ""
		}}}
	s := &Session{ID: "t", Mode: contracts.ModeReadonly}
	final, err := Run(context.Background(), cfg, s, "分析", func(Event) {})
	if err != nil {
		t.Fatal(err)
	}
	if final != "补充：不能做 X。" {
		t.Fatalf("final=%q", final)
	}
	if s.Messages[1].Phase != "commentary" || s.Messages[3].Phase != "final_answer" {
		t.Fatalf("reflected candidate was projected as a final answer: %+v", s.Messages)
	}
}

func TestRegisteredInferenceIsAvailableBeforeSelectingSkill(t *testing.T) {
	all := map[string]contracts.ToolDefinition{
		"inspect_record": {Name: "inspect_record"}, "preprocess": {Name: "preprocess"}, "run_model": {Name: "run_model"}, "recognize_emotion": {Name: "recognize_emotion"},
	}
	sk := &skills.Skill{Name: "x", AllowedTools: []string{"run_model"}}
	cfg := Config{AllTools: all, Skills: []*skills.Skill{sk}}
	s := &Session{}
	names := func() []string {
		var out []string
		for _, sp := range visibleToolSpecs(cfg, s) {
			out = append(out, sp.Function.Name)
		}
		return out
	}
	if got := names(); !strings.Contains(strings.Join(got, ","), "preprocess") || !strings.Contains(strings.Join(got, ","), "recognize_emotion") {
		t.Fatalf("registered task tool unavailable before selecting skill: %v", got)
	}
	s.ActiveSkill = sk
	if got := names(); !strings.Contains(strings.Join(got, ","), "run_model") {
		t.Fatalf("domain tool not visible with skill: %v", got)
	}
}

func TestNoSkillsIgnoresRestoredActiveSkill(t *testing.T) {
	s := &Session{ActiveSkill: &skills.Skill{Name: "old", AllowedTools: []string{"skill_only"}}}
	cfg := Config{AllTools: map[string]contracts.ToolDefinition{"inspect_record": {Name: "inspect_record"}, "skill_only": {Name: "skill_only"}}}
	for _, spec := range visibleToolSpecs(cfg, s) {
		if spec.Function.Name == "read_skill" || spec.Function.Name == "skill_only" {
			t.Fatalf("disabled skill leaked tool %s", spec.Function.Name)
		}
	}
}

func TestStopBetweenToolsKeepsProtocolAndDoesNotExecuteRemainingTools(t *testing.T) {
	client := fakeLLM(t, []string{sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"inspect_record","arguments":"{\"path\":\"a.npy\"}"}},{"index":1,"id":"c2","function":{"name":"inspect_record","arguments":"{\"path\":\"b.npy\"}"}}]}}]}`)})
	ex := &fakeExec{}
	cfg := Config{LLM: client, Executor: ex, AllTools: map[string]contracts.ToolDefinition{"inspect_record": {Name: "inspect_record"}}, SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s := &Session{ID: "test"}
	_, err := Run(ctx, cfg, s, "inspect", func(e Event) {
		if e.Type == "tool_execution_end" {
			cancel()
		}
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected stop, got %v", err)
	}
	if len(ex.calls) != 1 {
		t.Fatalf("executed tools after stop: %v", ex.calls)
	}
	if len(s.Messages) != 4 || s.Messages[1].Role != "assistant" || len(s.Messages[1].ToolCalls) != 2 || s.Messages[2].ToolCallID != "c1" || s.Messages[3].ToolCallID != "c2" {
		t.Fatalf("broken tool-call order: %+v", s.Messages)
	}
	if !strings.Contains(s.Messages[3].Content, "stopped") {
		t.Fatal(s.Messages[3].Content)
	}
}

func TestRetryRetainsCompletedToolsAndPartialAnswer(t *testing.T) {
	client := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"inspect_record","arguments":"{\"path\":\"x.npy\"}"}}]}}]}`),
		"data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n",
		sse(`{"choices":[{"delta":{"content":"finished"}}]}`),
	})
	ex := &fakeExec{}
	cfg := Config{LLM: client, Executor: ex, AllTools: map[string]contracts.ToolDefinition{"inspect_record": {Name: "inspect_record"}}, SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	s := &Session{ID: "test"}
	if _, err := Run(context.Background(), cfg, s, "inspect", func(Event) {}); err == nil {
		t.Fatal("truncated stream succeeded")
	}
	if s.Messages[len(s.Messages)-1].Content != "partial" {
		t.Fatal("partial response lost")
	}
	text, err := Run(context.Background(), cfg, s, "", func(Event) {})
	if err != nil || text != "finished" || len(ex.calls) != 1 {
		t.Fatalf("text=%s err=%v calls=%v", text, err, ex.calls)
	}
	users := 0
	for _, m := range s.Messages {
		if m.Role == "user" {
			users++
		}
	}
	if users != 1 {
		t.Fatalf("retry duplicated request: %+v", s.Messages)
	}
}

func TestEmptyCompletionIsNotReportedAsSuccess(t *testing.T) {
	client := fakeLLM(t, []string{sse(`{"choices":[{"delta":{},"finish_reason":"stop"}]}`)})
	_, _, _, err := streamTurn(context.Background(), client, nil, nil, func(Event) {})
	if err == nil {
		t.Fatal("empty completion was accepted")
	}
}

func TestDynamicContextFollowsHistory(t *testing.T) {
	s := &Session{Messages: []llm.Message{{Role: "user", Content: "分析数据"}}, Summaries: []string{"早期结论"}}
	cfg := Config{FileCtxFn: func(*Session) string { return "<file_context>新产物</file_context>" }}
	msgs := requestMessages(cfg, s, "SYS", false)
	if len(msgs) != 4 || msgs[0].Content != "SYS" || !strings.Contains(msgs[1].Content, "早期结论") || msgs[2].Content != "分析数据" || !strings.Contains(msgs[3].Content, "新产物") {
		t.Fatalf("cacheable history must precede dynamic context: %+v", msgs)
	}
}

func TestToolBudgetReportsEverySkippedCall(t *testing.T) {
	c := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"inspect_record","arguments":"{}"}},{"index":1,"id":"c2","function":{"name":"inspect_record","arguments":"{}"}},{"index":2,"id":"c3","function":{"name":"inspect_record","arguments":"{}"}}]}}]}`),
		sse(`{"choices":[{"delta":{"content":"完成两项，第三项待处理。"}}]}`),
	})
	ex := &fakeExec{}
	cfg := Config{LLM: c, Executor: ex, MaxToolsPer: 2, AllTools: map[string]contracts.ToolDefinition{"inspect_record": {Name: "inspect_record"}},
		SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	s := &Session{ID: "t"}
	if _, err := Run(context.Background(), cfg, s, "读取", func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if len(ex.calls) != 2 || len(s.Messages[1].ToolCalls) != 3 || s.Messages[4].ToolCallID != "c3" || !strings.Contains(s.Messages[4].Content, "tool_budget") {
		t.Fatalf("skipped call lost: calls=%v transcript=%+v", ex.calls, s.Messages)
	}
}

func TestModelUsageRecordsCacheAndElapsedTime(t *testing.T) {
	c := fakeLLM(t, []string{sse(`{"choices":[{"delta":{"content":"完成"}}]}`,
		`{"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":5,"prompt_tokens_details":{"cached_tokens":80}}}`)})
	cfg := Config{LLM: c, SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	s := &Session{ID: "t"}
	if _, err := Run(context.Background(), cfg, s, "分析", func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if len(s.Usage) != 1 || s.Usage[0].PromptTokens != 100 || s.Usage[0].CachedTokens == nil || *s.Usage[0].CachedTokens != 80 || !s.Usage[0].Completed {
		t.Fatalf("usage not recorded: %+v", s.Usage)
	}
}
