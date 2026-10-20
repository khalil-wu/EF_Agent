package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/worker"
)

func TestScriptProgressUsesWorkerEventsAndPreservesWorkspaceScope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/tools/run_python/events" {
			t.Errorf("script did not use the progress transport: %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var input struct {
			Args map[string]any `json:"args"`
		}
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
		}
		if input.Args["_session_id"] != "ses_script" || input.Args["_workspace_id"] != "ws_data" || input.Args["code"] != "print('batch')" {
			t.Errorf("script lost its scope: %+v", input.Args)
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		fmt.Fprintln(w, `{"type":"progress","progress":{"phase":"processing","completed":1,"total":2,"file":"subject-01.npz"}}`)
		w.(http.Flusher).Flush()
		fmt.Fprintln(w, `{"type":"result","result":{"status":"ok","files":["summary.csv"]}}`)
	}))
	defer server.Close()
	cfg := Config{Executor: worker.New(server.URL), AllTools: map[string]contracts.ToolDefinition{"run_python": {Name: "run_python"}}}
	var events []Event
	result, failed := executeOne(context.Background(), cfg, &Session{ID: "ses_script", WorkspaceID: "ws_data"}, ToolCall{ID: "c_script", Name: "run_python", Args: json.RawMessage(`{"code":"print('batch')"}`)}, func(event Event) {
		events = append(events, event)
	})
	if failed || string(result) != `{"status":"ok","files":["summary.csv"]}` || len(events) != 3 {
		t.Fatalf("script failed or lost its progress: result=%s events=%+v", result, events)
	}
	progress := events[1]
	if events[0].Type != "tool_execution_start" || progress.Type != "tool_execution_progress" || events[2].Type != "tool_execution_end" || progress.ToolCallID != "c_script" || progress.ToolName != "run_python" {
		t.Fatalf("script progress is not attached to its tool call: %+v", events)
	}
	var measured struct {
		Completed int    `json:"completed"`
		File      string `json:"file"`
	}
	if json.Unmarshal(progress.Result, &measured) != nil || measured.Completed != 1 || measured.File != "subject-01.npz" {
		t.Fatalf("actual worker progress was changed: %s", progress.Result)
	}
}
