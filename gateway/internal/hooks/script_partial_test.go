package hooks

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/prompt"
)

type partialScriptExecutor struct{ result json.RawMessage }

func (executor partialScriptExecutor) Call(context.Context, string, json.RawMessage) (json.RawMessage, error) {
	return executor.result, fmt.Errorf("script failed after completing one file")
}

func TestFailedScriptKeepsCompletedOutputsAndRemainsAnError(t *testing.T) {
	requests := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		requests++
		if requests == 1 {
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c_script\",\"function\":{\"name\":\"run_python\",\"arguments\":\"{}\"}}]}}]}\n\ndata: [DONE]\n\n")
			return
		}
		var input struct{ Messages []llm.Message }
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
		}
		foundError, foundResult := false, false
		for _, message := range input.Messages {
			foundError = foundError || message.Role == "tool" && strings.Contains(message.Content, `"status":"error"`) && strings.Contains(message.Content, "second file failed")
			foundResult = foundResult || strings.Contains(message.Content, "<runtime_context>") && strings.Contains(message.Content, "art_completed")
		}
		if !foundError || !foundResult {
			t.Errorf("next model request lost the error or its completed result: %+v", input.Messages)
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"首个文件已完成，第二个文件需要修正。\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer model.Close()
	all := map[string]contracts.ToolDefinition{"run_python": {Name: "run_python", PermissionModeMin: "readonly"}}
	body, err := json.Marshal(map[string]any{"status": "error", "error": map[string]string{"code": "ValueError", "message": "second file failed"},
		"manifests": []map[string]string{{"manifest_id": "sig_completed", "attachment_sha256": "completed", "modality": "eeg"}},
		"artifacts": []map[string]any{{"artifact_id": "art_completed", "kind": "table", "uri": "completed.csv", "summary": map[string]int{"rows": 1}}},
		"files":     []string{"run_001.out"}, "stdout": strings.Repeat("completed first file\n", 1000)})
	if err != nil {
		t.Fatal(err)
	}
	session := &agent.Session{ID: "ses_script", Mode: contracts.ModeReadonly}
	config := agent.Config{LLM: llm.New(model.URL, "", "test"), Executor: partialScriptExecutor{body}, AllTools: all,
		SystemFn: func(*agent.Session) string { return "system" },
		FileCtxFn: func(s *agent.Session) string {
			return prompt.FileContext(s.Manifests) + prompt.RuntimeContext(s.Artifacts, s.ScratchFiles)
		}, Hooks: Build(all, t.TempDir(), nil)}
	var events []agent.Event
	if _, err := agent.Run(context.Background(), config, session, "批量分析", func(event agent.Event) { events = append(events, event) }); err != nil {
		t.Fatal(err)
	}
	if len(session.Manifests) != 1 || len(session.Artifacts) != 1 || len(session.ScratchFiles) != 1 || session.ToolErrors != 1 {
		t.Fatalf("partial outputs or script error were lost: %+v", session)
	}
	failed, ready := false, false
	for _, event := range events {
		failed = failed || event.Type == "tool_execution_end" && event.ToolCallID == "c_script" && event.IsError
		ready = ready || event.Type == "artifact_ready" && strings.Contains(string(event.Result), "art_completed")
		if event.Type == "tool_execution_end" && string(event.Result) != string(body) {
			t.Error("UI lost the full script result when its model context was shortened")
		}
	}
	if !failed || !ready {
		t.Fatalf("UI did not receive both the error and completed output: %+v", events)
	}
}
