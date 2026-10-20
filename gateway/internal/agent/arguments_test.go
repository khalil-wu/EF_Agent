package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"eegagent/gateway/internal/contracts"
)

func TestTruncatedToolArgumentsAreRejectedAndTheModelCanCorrectThem(t *testing.T) {
	raw := `{"code":"` + strings.Repeat("x", 3177)
	chunk := func(id, arguments string) string {
		return fmt.Sprintf(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":%q,"function":{"name":"run_python","arguments":%q}}]},"finish_reason":"tool_calls"}]}`, id, arguments)
	}
	model := fakeLLM(t, []string{
		sse(chunk("bad_call", raw)),
		sse(chunk("corrected_call", `{"code":"print(1)"}`)),
		sse(`{"choices":[{"delta":{"content":"已修正参数并完成执行。"}}]}`),
	})
	executor := &fakeExec{}
	beforeCalls := 0
	cfg := Config{LLM: model, Executor: executor, AllTools: map[string]contracts.ToolDefinition{"run_python": {Name: "run_python", InputSchema: json.RawMessage(`{"type":"object"}`)}},
		SystemFn: func(*Session) string { return "system" }, FileCtxFn: func(*Session) string { return "files" },
		Hooks: Hooks{BeforeTool: func(context.Context, *Session, ToolCall) (bool, string) { beforeCalls++; return false, "" }},
	}
	session := &Session{ID: "ses_args", Mode: contracts.ModeReadonly}
	var events []Event
	answer, err := Run(context.Background(), cfg, session, "运行脚本", func(event Event) { events = append(events, event) })
	if err != nil || answer != "已修正参数并完成执行。" {
		t.Fatal(answer, err)
	}
	if len(executor.calls) != 1 || beforeCalls != 1 || strings.Contains(executor.args[0], raw) {
		t.Fatal("invalid arguments reached execution", executor.calls, beforeCalls)
	}
	call := session.Messages[1].ToolCalls[0]
	if call.Function.Arguments != raw || call.ArgumentsError == "" {
		t.Fatal("raw rejected input or its parse error was lost")
	}
	rejected := 0
	for _, event := range events {
		if _, err := json.Marshal(event); err != nil {
			t.Fatalf("event is not JSON serializable: %s: %v", event.Type, err)
		}
		if event.ToolCallID == "bad_call" && event.Type == "tool_execution_start" {
			t.Fatal("rejected input was presented as running")
		}
		if event.ToolCallID == "bad_call" && event.Type == "tool_execution_end" {
			rejected++
			if !event.IsError || event.ArgumentsError == "" || event.RawArguments != raw || len(event.Args) != 0 {
				t.Fatal("rejection event lost its evidence")
			}
		}
	}
	if rejected != 1 {
		t.Fatal("missing rejection event", rejected)
	}
}

func TestOutputLimitRejectsTheWholeToolBatchAndCanRegenerate(t *testing.T) {
	model := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"limited","function":{"name":"run_python","arguments":"{\"code\":\"print(1)\"}"}}]},"finish_reason":"length"}]}`),
		sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"complete","function":{"name":"run_python","arguments":"{\"code\":\"print(1)\"}"}}]},"finish_reason":"tool_calls"}]}`),
		sse(`{"choices":[{"delta":{"content":"完成"},"finish_reason":"stop"}]}`),
	})
	executor := &fakeExec{}
	cfg := Config{LLM: model, Executor: executor,
		AllTools: map[string]contracts.ToolDefinition{"run_python": {Name: "run_python"}},
		SystemFn: func(*Session) string { return "system" }, FileCtxFn: func(*Session) string { return "context" }}
	session := &Session{ID: "ses_limit", Mode: contracts.ModeReadonly}
	answer, err := Run(context.Background(), cfg, session, "执行", func(Event) {})
	if err != nil || answer != "完成" || len(executor.calls) != 1 {
		t.Fatal(answer, err, executor.calls)
	}
	if !strings.Contains(session.Messages[2].Content, "输出长度上限") {
		t.Fatal(session.Messages)
	}
}

func TestOutputLimitContinuesTextWithoutMarkingPartialAnswerComplete(t *testing.T) {
	model := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"content":"未完的分析"},"finish_reason":"length"}],"usage":{"completion_tokens":10}}`),
		sse(`{"choices":[{"delta":{"content":"剩余结果已完成"},"finish_reason":"stop"}]}`),
	})
	cfg := Config{LLM: model, SystemFn: func(*Session) string { return "system" }, FileCtxFn: func(*Session) string { return "context" }}
	session := &Session{ID: "ses_text_limit", Mode: contracts.ModeReadonly}
	var phases []string
	answer, err := Run(context.Background(), cfg, session, "分析", func(event Event) {
		if event.Type == "message_completed" {
			phases = append(phases, event.Phase)
		}
	})
	if err != nil || answer != "剩余结果已完成" || strings.Join(phases, ",") != "interrupted,final_answer" {
		t.Fatal(answer, err, phases)
	}
	if session.Usage[0].Completed || session.Usage[0].CompletionTokens != 10 {
		t.Fatal(session.Usage)
	}
}
