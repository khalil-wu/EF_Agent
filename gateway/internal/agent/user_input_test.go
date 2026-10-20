package agent

import (
	"context"
	"encoding/json"
	"testing"
)

func TestUserQuestionPausesBeforeOtherTools(t *testing.T) {
	args := `{"questions":[{"id":"rate","question":"采样率是多少？","answer_type":"number","options":[{"label":"128 Hz"},{"label":"256 Hz"}]}]}`
	body, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"delta": map[string]any{"tool_calls": []any{
		map[string]any{"index": 0, "id": "ask_1", "function": map[string]any{"name": "request_user_input", "arguments": args}},
		map[string]any{"index": 1, "id": "plot_1", "function": map[string]any{"name": "plot", "arguments": `{"source_id":"sig_unknown"}`}},
	}}}}})
	client := fakeLLM(t, []string{sse(string(body)), sse(`{"choices":[{"delta":{"content":"收到，采样率为 128 Hz。"}}]}`)})
	executor := &fakeExec{}
	cfg := Config{LLM: client, Executor: executor, SystemFn: func(*Session) string { return "system" }, FileCtxFn: func(*Session) string { return "context" }}
	s := &Session{}
	var requested bool
	_, err := Run(context.Background(), cfg, s, "画功率谱", func(e Event) { requested = requested || e.Type == "user_input_requested" })
	if err != nil || !requested || s.PendingInput == nil || len(executor.calls) != 0 {
		t.Fatalf("question did not pause: %v %+v %v", err, s.PendingInput, executor.calls)
	}
	if len(s.Messages) != 4 || s.Messages[3].Role != "tool" || s.Messages[3].ToolCallID != "plot_1" {
		t.Fatal("pending batch must preserve matched tool results", s.Messages)
	}
	answer, err := s.PendingInput.AnswerText(map[string]string{"rate": "128 Hz"})
	if err != nil {
		t.Fatal(err)
	}
	text, err := Run(context.Background(), cfg, s, answer, func(Event) {})
	if err != nil || text != "收到，采样率为 128 Hz。" {
		t.Fatal(text, err)
	}
	if s.PendingInput != nil {
		t.Fatal("answered question remains pending")
	}
}

func TestUserQuestionRequiresValidQuestionsAndCompleteAnswers(t *testing.T) {
	for _, args := range []string{`{"questions":[]}`, `{"questions":[{"id":"x","question":"","answer_type":"text"}]}`, `{"questions":[{"id":"x","question":"a","answer_type":"text"},{"id":"x","question":"b","answer_type":"text"}]}`} {
		if _, err := newInputRequest("call", json.RawMessage(args)); err == nil {
			t.Fatal("invalid question accepted", args)
		}
	}
	request, err := newInputRequest("call", json.RawMessage(`{"questions":[{"id":"x","question":"采样率","answer_type":"number"}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := request.AnswerText(map[string]string{}); err == nil {
		t.Fatal("missing answer accepted")
	}
}

func TestMeasuredValueUsesFreeInputAndCategoriesUseChoices(t *testing.T) {
	request, err := newInputRequest("rate", json.RawMessage(`{"questions":[{"id":"rate","question":"采样率","answer_type":"number","options":[{"label":"猜测值"}]}]}`))
	if err != nil || len(request.Questions[0].Options) != 0 {
		t.Fatal("numeric input must not suggest guessed values", request, err)
	}
	request, err = newInputRequest("role", json.RawMessage(`{"questions":[{"id":"role","question":"视频用途","answer_type":"choice","options":[{"label":"面部记录"},{"label":"刺激视频"}]}]}`))
	if err != nil || len(request.Questions[0].Options) != 2 {
		t.Fatal(request, err)
	}
}
