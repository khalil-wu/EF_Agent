package agent

import (
	"context"
	"testing"

	"eegagent/gateway/internal/contracts"
)

func TestPublicMessageLifecycleStreamsAndKeepsPrivateReasoningOffTheWire(t *testing.T) {
	client := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"reasoning_content":"PRIVATE_REVIEW"}}]}`,
			`{"choices":[{"delta":{"reasoning_content":"PRIVATE_MORE"}}]}`,
			`{"choices":[{"delta":{"content":"我先"}}]}`,
			`{"choices":[{"delta":{"content":"检查记录。"}}]}`,
			`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"inspect_record","arguments":"{"}}]}}]}`,
			`{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"}"}}]}}]}`),
		sse(`{"choices":[{"delta":{"content":"记录含 "}}]}`,
			`{"choices":[{"delta":{"content":"8 个通道。"}}]}`),
	})
	var events []Event
	s := &Session{ID: "ses_abcdef"}
	cfg := Config{LLM: client, Executor: &fakeExec{}, AllTools: map[string]contracts.ToolDefinition{"inspect_record": {Name: "inspect_record"}},
		SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	if _, err := Run(context.Background(), cfg, s, "inspect", func(event Event) { events = append(events, event) }); err != nil {
		t.Fatal(err)
	}
	started, deltas, completed := map[string]int{}, map[string]int{}, map[string]string{}
	toolPrepared, toolExecuted := -1, -1
	thinkingEvents := 0
	for index, event := range events {
		if event.Type == "reasoning_delta" || event.Text == "PRIVATE_REVIEW" {
			t.Fatalf("private reasoning reached the public event stream: %+v", event)
		}
		switch event.Type {
		case "model_activity":
			thinkingEvents++
			if event.Phase != "thinking" || event.Text != "" || started[event.MessageID] != 1 {
				t.Fatalf("thinking status lost its message or exposed reasoning: %+v", event)
			}
		case "message_started":
			if event.MessageID == "" || event.Phase != "pending" {
				t.Fatalf("message start has no stable identity: %+v", event)
			}
			started[event.MessageID]++
		case "message_delta":
			if started[event.MessageID] != 1 || completed[event.MessageID] != "" {
				t.Fatalf("text did not arrive between message start and completion: %+v", event)
			}
			deltas[event.MessageID]++
		case "message_phase":
			if event.Phase != "commentary" || event.MessageID != s.Messages[1].MessageID {
				t.Fatalf("tool text was not classified as public commentary: %+v", event)
			}
		case "tool_call_started":
			toolPrepared = index
			if event.ToolCallID != "c1" || event.ToolName != "inspect_record" || len(event.Args) != 0 {
				t.Fatalf("tool preparation exposed partial arguments: %+v", event)
			}
		case "tool_execution_start":
			toolExecuted = index
		case "message_completed":
			completed[event.MessageID] = event.Phase
		}
	}
	if len(started) != 2 || len(completed) != 2 || toolPrepared < 0 || toolPrepared >= toolExecuted {
		t.Fatalf("incomplete streaming lifecycle: %+v", events)
	}
	if thinkingEvents != 1 {
		t.Fatalf("expected one thinking phase, got %d", thinkingEvents)
	}
	for _, index := range []int{1, 3} {
		message := s.Messages[index]
		if deltas[message.MessageID] != 2 || completed[message.MessageID] != message.Phase {
			t.Fatalf("stream and transcript differ: %+v", message)
		}
	}
}
