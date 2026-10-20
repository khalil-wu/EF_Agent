package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
)

func TestConversationModelSelectionPersistsAndRoutesOnlyItsSession(t *testing.T) {
	var received []string
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-key" {
			t.Error("provider authentication missing")
		}
		if r.URL.Path == "/models" {
			fmt.Fprint(w, `{"data":[{"id":"model-a"},{"id":"model-b"}]}`)
			return
		}
		var input struct {
			Model string `json:"model"`
		}
		json.NewDecoder(r.Body).Decode(&input)
		received = append(received, input.Model)
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"完成\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer provider.Close()
	cfg := agent.Config{LLM: llm.New(provider.URL, "test-key", "model-a"), SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	directory := t.TempDir()
	server := New(cfg, nil, directory)
	server.ModelName = "model-a"
	handler := server.Routes()
	first, second := create(t, handler), create(t, handler)
	list := request(t, handler, "GET", "/llm/models", "")
	if list.Code != 200 || !strings.Contains(list.Body.String(), "model-b") {
		t.Fatal(list.Body.String())
	}
	choice := request(t, handler, "PATCH", "/sessions/"+first+"/model", `{"model":"model-b"}`)
	if choice.Code != 200 {
		t.Fatal(choice.Body.String())
	}
	if cfg.LLM.Model != "model-a" {
		t.Fatal("selection mutated the shared provider")
	}
	restarted := New(cfg, nil, directory)
	restarted.ModelName = "model-a"
	handler = restarted.Routes()
	snapshot := request(t, handler, "GET", "/sessions/"+first, "")
	if !strings.Contains(snapshot.Body.String(), `"llm_model":"model-b"`) {
		t.Fatal(snapshot.Body.String())
	}
	request(t, handler, "POST", "/sessions/"+first+"/messages", `{"text":"继续"}`)
	request(t, handler, "POST", "/sessions/"+second+"/messages", `{"text":"另一个会话"}`)
	if len(received) != 2 || received[0] != "model-b" || received[1] != "model-a" {
		t.Fatal(received)
	}
	invalid := request(t, handler, "PATCH", "/sessions/"+first+"/model", `{"model":"unsupported"}`)
	if invalid.Code != 400 {
		t.Fatal(invalid.Code)
	}
	loaded, err := session.Load(directory, first, nil)
	if err != nil || loaded.LLMModel != "model-b" {
		t.Fatal(loaded, err)
	}
	loaded.Messages = []llm.Message{{Role: "user", Content: "旧问题"}, {Role: "assistant", Content: "旧回答"}, {Role: "user", Content: "另一个问题"}, {Role: "assistant", Content: "回答"}, {Role: "user", Content: "新问题"}, {Role: "assistant", Content: "新回答"}}
	if err := agent.DefaultCompactor(cfg.LLM, 1, 2, func(current *agent.Session) int { return agent.EstimateTokens(current.Messages) })(context.Background(), loaded, func(agent.Event) {}); err != nil {
		t.Fatal(err)
	}
	if received[len(received)-1] != "model-b" {
		t.Fatal("compaction used a different model", received)
	}
}

func TestProviderModelListFailureDoesNotReturnInventedModels(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
	defer provider.Close()
	server := New(agent.Config{LLM: llm.New(provider.URL, "", "default")}, nil, t.TempDir())
	response := request(t, server.Routes(), "GET", "/llm/models", "")
	if response.Code != 502 || !strings.Contains(response.Body.String(), "503") {
		t.Fatal(response.Code, response.Body.String())
	}
}
