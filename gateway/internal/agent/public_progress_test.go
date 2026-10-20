package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/worker"
)

func TestAnalysisRequestsGuidePublicPlansAndObservedProgress(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request struct {
			Messages []llm.Message `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
		}
		latest := request.Messages[len(request.Messages)-1].Content
		w.Header().Set("Content-Type", "text/event-stream")
		switch requests {
		case 0:
			if !strings.Contains(latest, "第一批分析工具调用前") || !strings.Contains(latest, "公开工作计划") {
				t.Errorf("initial request has no concrete public planning guidance: %s", latest)
			}
			fmt.Fprint(w, sse(`{"choices":[{"delta":{"reasoning_content":"PRIVATE_REVIEW"}}]}`,
				`{"choices":[{"delta":{"content":"我先分解 ICA 成分，再比较处理前后的波形。","tool_calls":[{"index":0,"id":"ica","function":{"name":"clean_artifacts","arguments":"{\"source_id\":\"sig_test\",\"method\":\"ica_iclabel\"}"}}]}}]}`))
		case 1:
			if !strings.Contains(latest, "一项具体发现") || !strings.Contains(latest, "下一步") {
				t.Errorf("analysis result did not request an observed progress update: %s", latest)
			}
			fmt.Fprint(w, sse(`{"choices":[{"delta":{"content":"ICA 移除了 3 个成分，处理后的信号已保存。"}}]}`))
		default:
			t.Error("progress introduced an extra model request")
		}
		requests++
	}))
	defer server.Close()
	var events []Event
	s := &Session{ID: "ses_progress"}
	cfg := Config{LLM: llm.New(server.URL, "", "fake"), Executor: responseExec{json.RawMessage(`{"status":"ok","summary":{"n_removed":3}}`)},
		AllTools: map[string]contracts.ToolDefinition{"clean_artifacts": {Name: "clean_artifacts"}},
		SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	if _, err := Run(context.Background(), cfg, s, "清理这段信号并比较前后", func(event Event) { events = append(events, event) }); err != nil {
		t.Fatal(err)
	}
	plan, execution := -1, -1
	for i, event := range events {
		if strings.Contains(event.Text, "PRIVATE_REVIEW") {
			t.Fatalf("provider reasoning reached public progress: %+v", event)
		}
		if event.Type == "message_delta" && strings.Contains(event.Text, "我先分解") {
			plan = i
		}
		if event.Type == "tool_execution_start" {
			execution = i
		}
	}
	if plan < 0 || execution <= plan || requests != 2 || s.Messages[1].Phase != "commentary" {
		t.Fatalf("plan must be public before execution, with no extra request: plan=%d execution=%d requests=%d", plan, execution, requests)
	}
}

func TestPreparationResultsDoNotRepeatThePublicAnalysisPlan(t *testing.T) {
	s := &Session{Turn: 2, Messages: []llm.Message{{Role: "assistant", Content: "先核对采集参数。"}, {Role: "tool", Name: "read_skill", Content: "instructions"}}}
	if got := publicProgressContext(s); got != "" {
		t.Fatalf("reading a skill should not demand another progress message: %s", got)
	}
}

func TestSilentPreparationKeepsTheUnsentPublicPlanResponsibility(t *testing.T) {
	s := &Session{Turn: 2, Messages: []llm.Message{{Role: "user", Content: "检查并清理这段 EEG"}, {Role: "assistant"}, {Role: "tool", Name: "read_skill", Content: "instructions"}}}
	if got := publicProgressContext(s); !strings.Contains(got, "尚未发出公开工作计划") {
		t.Fatalf("a silent skill lookup consumed the public plan: %s", got)
	}
	s.Messages = append(s.Messages, llm.Message{Role: "assistant", Phase: "commentary", Content: "先比较原始波形与工频，再选择清理方法。"}, llm.Message{Role: "tool", Name: "read_skill", Content: "reference"})
	if got := publicProgressContext(s); got != "" {
		t.Fatalf("an already visible plan was repeated: %s", got)
	}
}

func TestCleaningProgressReachesThePublicToolTimeline(t *testing.T) {
	stages := []string{
		`{"stage":"clean_load","label":"读取待清洗信号","detail":"32 通道"}`,
		`{"stage":"ica_fit","label":"拟合 ICA 成分","detail":"31 个成分"}`,
		`{"stage":"ica_label","label":"识别独立成分类别","detail":"ICLabel"}`,
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/tools/clean_artifacts/events" {
			t.Errorf("cleaning bypassed actual worker progress: %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var input struct {
			Args map[string]any `json:"args"`
		}
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
		}
		if input.Args["_session_id"] != "ses_progress" || input.Args["_workspace_id"] != "ws_data" || input.Args["method"] != "ica_iclabel" {
			t.Errorf("cleaning progress lost the original call scope: %+v", input.Args)
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		for _, stage := range stages {
			fmt.Fprintf(w, "{\"type\":\"progress\",\"progress\":%s}\n", stage)
			w.(http.Flusher).Flush()
		}
		fmt.Fprintln(w, `{"type":"result","result":{"status":"ok","summary":{"n_removed":3}}}`)
	}))
	defer server.Close()
	cfg := Config{Executor: worker.New(server.URL), AllTools: map[string]contracts.ToolDefinition{"clean_artifacts": {Name: "clean_artifacts"}}}
	var events []Event
	_, failed := executeOne(context.Background(), cfg, &Session{ID: "ses_progress", WorkspaceID: "ws_data"},
		ToolCall{ID: "ica", Name: "clean_artifacts", Args: json.RawMessage(`{"source_id":"sig_test","method":"ica_iclabel"}`)},
		func(event Event) { events = append(events, event) })
	if failed || len(events) != len(stages)+2 {
		t.Fatalf("cleaning stages were lost: %+v", events)
	}
	for i, stage := range stages {
		event := events[i+1]
		if event.Type != "tool_execution_progress" || event.ToolCallID != "ica" || event.ToolName != "clean_artifacts" || string(event.Result) != stage {
			t.Fatalf("worker stage was changed or detached: %+v", event)
		}
	}
}
