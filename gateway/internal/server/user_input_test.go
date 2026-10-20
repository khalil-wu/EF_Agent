package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
)

func TestQuestionSurvivesRestartAndAnswersResumeConversation(t *testing.T) {
	calls := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.Header().Set("Content-Type", "text/event-stream")
		if calls == 1 {
			args := `{"questions":[{"id":"rate","question":"采样率是多少？","answer_type":"number"}]}`
			body, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"delta": map[string]any{"tool_calls": []any{map[string]any{"index": 0, "id": "ask_rate", "function": map[string]any{"name": "request_user_input", "arguments": args}}}}}}})
			fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", body)
			return
		}
		var body struct {
			Messages []llm.Message `json:"messages"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		found := false
		for _, message := range body.Messages {
			found = found || message.Role == "user" && strings.Contains(message.Content, "128 Hz")
		}
		if !found {
			t.Error("user answer missing from resumed model context")
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"继续分析。\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer model.Close()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	dir := t.TempDir()
	server := New(cfg, nil, dir)
	id := create(t, server.Routes())
	first := request(t, server.Routes(), "POST", "/sessions/"+id+"/messages", `{"text":"画功率谱"}`)
	if !strings.Contains(first.Body.String(), "event: user_input_requested") {
		t.Fatal(first.Body.String())
	}
	restarted := New(cfg, nil, dir).Routes()
	snapshot := request(t, restarted, "GET", "/sessions/"+id, "")
	if !strings.Contains(snapshot.Body.String(), `"pending_input":{"id":"ask_rate"`) {
		t.Fatal(snapshot.Body.String())
	}
	bad := request(t, restarted, "POST", "/sessions/"+id+"/messages", `{"input_request_id":"ask_rate","answers":{}}`)
	if bad.Code != 400 || calls != 1 {
		t.Fatal(bad.Code, calls)
	}
	wrong := request(t, restarted, "POST", "/sessions/"+id+"/messages", `{"input_request_id":"old","answers":{"rate":"128 Hz"}}`)
	if wrong.Code != 409 {
		t.Fatal(wrong.Code)
	}
	answer := request(t, restarted, "POST", "/sessions/"+id+"/messages", `{"input_request_id":"ask_rate","answers":{"rate":"128 Hz"}}`)
	if calls != 2 || !strings.Contains(answer.Body.String(), "继续分析") {
		t.Fatal(calls, answer.Body.String())
	}
	if strings.Contains(request(t, restarted, "GET", "/sessions/"+id, "").Body.String(), `"pending_input":{"id"`) {
		t.Fatal("answered question remains pending")
	}
	var persisted struct {
		Messages []llm.Message `json:"messages"`
	}
	json.Unmarshal(request(t, restarted, "GET", "/sessions/"+id, "").Body.Bytes(), &persisted)
	var reply *llm.Message
	for index := range persisted.Messages {
		if persisted.Messages[index].Role == "user" && persisted.Messages[index].Presentation != nil {
			reply = &persisted.Messages[index]
		}
	}
	if reply == nil || reply.Presentation.Text != "128 Hz" || len(reply.Presentation.Questions) != 1 || !strings.Contains(reply.Content, "采样率是多少") {
		t.Fatal("question answer presentation was not preserved separately", reply)
	}
}
