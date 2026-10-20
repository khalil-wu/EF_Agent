package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestArgumentParsingRequiresACompleteObject(t *testing.T) {
	for _, raw := range []string{`{"code":"unfinished`, `[]`, `null`, `"text"`} {
		if ToolArgumentsError(json.RawMessage(raw)) == "" {
			t.Errorf("accepted non-executable arguments: %s", raw)
		}
	}
	for _, raw := range []string{`{}`, `{"code":"print(1)"}`} {
		if message := ToolArgumentsError(json.RawMessage(raw)); message != "" {
			t.Error(message)
		}
	}
}

func TestArgumentErrorsStayInHistoryAndOutOfProviderRequests(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload struct {
			Messages []map[string]any `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		call := payload.Messages[0]["tool_calls"].([]any)[0].(map[string]any)
		if _, ok := call["arguments_error"]; ok {
			t.Error("UI parse metadata reached provider")
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"corrected\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer backend.Close()
	call := ToolCall{ID: "bad", Type: "function", ArgumentsError: "parse failed"}
	call.Function.Name, call.Function.Arguments = "run_python", `{"code":"unfinished`
	messages := []Message{{Role: "assistant", ToolCalls: []ToolCall{call}}}
	events := make(chan Event, 8)
	go New(backend.URL, "", "fixture").Stream(context.Background(), messages, nil, events)
	for event := range events {
		if event.Type == "error" {
			t.Fatal(event.Err)
		}
	}
	if messages[0].ToolCalls[0].ArgumentsError != "parse failed" {
		t.Fatal("request preparation modified the saved history")
	}
}
