package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
)

func TestTurnBudgetReturnsRecoverablePartialAndKeepsCompletedResults(t *testing.T) {
	requests := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Messages []llm.Message  `json:"messages"`
			Tools    []llm.ToolSpec `json:"tools"`
		}
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
		}
		requests++
		w.Header().Set("Content-Type", "text/event-stream")
		if requests == 1 {
			fmt.Fprint(w, sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c_completed","function":{"name":"run_python","arguments":"{}"}}]}}]}`))
			return
		}
		reflection := false
		for _, message := range input.Messages {
			reflection = reflection || message.Role == "user" && strings.HasPrefix(message.Content, "<reflection>")
		}
		if len(input.Tools) != 0 || !reflection || requests != 2 {
			t.Errorf("budget summary must use internal context with tools stopped: %+v", input)
		}
		fmt.Fprint(w, sse(`{"choices":[{"delta":{"content":"已生成特征表；工作区结果文件尚未保存，这是未完成的部分结果。"}}]}`))
	}))
	defer model.Close()
	completed := json.RawMessage(`{"status":"ok","artifact":{"artifact_id":"art_abcdef123456","kind":"table","uri":"features.csv","summary":{"rows":2}}}`)
	var saved []llm.Message
	config := Config{LLM: llm.New(model.URL, "", "test"), Executor: responseExec{completed}, MaxTurns: 1,
		AllTools: map[string]contracts.ToolDefinition{"run_python": {Name: "run_python"}},
		SystemFn: func(*Session) string { return "system" }, FileCtxFn: func(*Session) string { return "context" },
		Checkpoint: func(s *Session) error { saved = append([]llm.Message(nil), s.Messages...); return nil }}
	session := &Session{ID: "ses_budget"}
	var events []Event
	partial, err := Run(context.Background(), config, session, "提取特征并保存到工作区", func(event Event) { events = append(events, event) })
	if !errors.Is(err, ErrTurnBudget) || partial == "" || requests != 2 || saved[len(saved)-1].Phase != "interrupted" {
		t.Fatalf("budget exhaustion was reported as a completed task: text=%q error=%v saved=%+v", partial, err, saved)
	}
	keptResult, partialEvent := false, false
	for _, message := range saved {
		keptResult = keptResult || message.Role == "tool" && message.ToolCallID == "c_completed" && message.Content == string(completed)
		if message.Phase == "final_answer" {
			t.Fatal("unfinished task was saved as a final answer")
		}
	}
	for _, event := range events {
		partialEvent = partialEvent || event.Type == "message_completed" && event.Phase == "interrupted"
	}
	if !keptResult || !partialEvent || session.Usage[len(session.Usage)-1].Kind != "partial" {
		t.Fatalf("completed output or partial projection was lost: saved=%+v events=%+v", saved, events)
	}
}
