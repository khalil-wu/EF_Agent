package session

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
)

type deliveryExecutor struct {
	body   json.RawMessage
	failed bool
}

func (executor deliveryExecutor) Call(context.Context, string, json.RawMessage) (json.RawMessage, error) {
	if executor.failed {
		return executor.body, errors.New("script failed after saving outputs")
	}
	return executor.body, nil
}

func TestCompleteDeliveriesSurviveSnapshotWhileProviderReceivesOnlyBudgetResult(t *testing.T) {
	for _, failed := range []bool{false, true} {
		t.Run(fmt.Sprint("partial_error=", failed), func(t *testing.T) {
			files, links := []map[string]string{}, []map[string]string{}
			for index := 1; index <= 40; index++ {
				identifier := fmt.Sprintf("art_%012x", index)
				files = append(files, map[string]string{"workspace_id": "ws_test", "path": fmt.Sprintf("results/trial_%02d.npy", index), "name": fmt.Sprintf("trial_%02d.npy", index), "artifact_id": identifier})
				links = append(links, map[string]string{"artifact_id": identifier, "download_url": "/artifacts/" + identifier + "/file"})
			}
			status := "ok"
			if failed {
				status = "error"
			}
			full, _ := json.Marshal(map[string]any{"status": status, "workspace_files": files, "result_links": links})
			brief, _ := json.Marshal(map[string]any{"status": status, "workspace_files": files[:8], "workspace_files_count": 40, "workspace_files_omitted": true, "result_links": links[:8]})
			requests := []string{}
			provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				body, _ := io.ReadAll(r.Body)
				requests = append(requests, string(body))
				if len(requests) == 1 {
					fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"save_batch\",\"function\":{\"name\":\"run_python\",\"arguments\":\"{\\\"code\\\":\\\"save_batch()\\\"}\"}}]}}]}\n\ndata: [DONE]\n\n")
				} else {
					fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Saved outputs are ready.\"}}]}\n\ndata: [DONE]\n\n")
				}
			}))
			defer provider.Close()
			cfg := agent.Config{LLM: llm.New(provider.URL, "", "model"), Executor: deliveryExecutor{full, failed},
				AllTools: map[string]contracts.ToolDefinition{"run_python": {Name: "run_python", InputSchema: json.RawMessage(`{"type":"object"}`)}},
				SystemFn: func(*agent.Session) string { return "Run the task." }, FileCtxFn: func(*agent.Session) string { return "" },
				Hooks: agent.Hooks{AfterTool: func(_ context.Context, _ *agent.Session, _ agent.ToolCall, _ json.RawMessage, isErr bool) (json.RawMessage, bool) {
					return brief, isErr
				}}}
			state := &agent.Session{ID: "ses_abcdef123456", Mode: contracts.ModeReadonly}
			var streamed json.RawMessage
			_, err := agent.Run(context.Background(), cfg, state, "Save all trial outputs.", func(event agent.Event) {
				if event.Type == "tool_execution_end" {
					streamed = event.Result
				}
			})
			if err != nil {
				t.Fatal(err)
			}
			if len(requests) != 2 || strings.Contains(requests[1], "tool_presentation") || strings.Contains(requests[1], "trial_40.npy") {
				t.Fatal(requests)
			}
			storage := t.TempDir()
			if err := Save(storage, state); err != nil {
				t.Fatal(err)
			}
			restored, err := Load(storage, state.ID, nil)
			if err != nil {
				t.Fatal(err)
			}
			message := restored.Messages[2]
			var complete, live struct {
				WorkspaceFiles []json.RawMessage `json:"workspace_files"`
				ResultLinks    []json.RawMessage `json:"result_links"`
			}
			if err := json.Unmarshal(message.ToolPresentation, &complete); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(streamed, &live); err != nil {
				t.Fatal(err)
			}
			if len(complete.WorkspaceFiles) != 40 || len(complete.ResultLinks) != 40 || len(live.WorkspaceFiles) != 40 || !strings.Contains(string(message.ToolPresentation), "trial_40.npy") {
				t.Fatal(string(message.ToolPresentation))
			}
			if message.Content != string(brief) {
				t.Fatal("model result was replaced by UI data")
			}
			if (state.ToolErrors > 0) != failed {
				t.Fatal("partial script failure state was lost")
			}
		})
	}
}
