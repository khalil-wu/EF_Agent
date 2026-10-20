package agent

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/llm"
)

func TestFullRequestBudgetIncludesDynamicContextAndTools(t *testing.T) {
	s := &Session{Summaries: []string{"已有摘要"}}
	for i := 0; i < 8; i++ {
		s.Messages = append(s.Messages, llm.Message{Role: "user", Content: "短消息"})
	}
	cfg := Config{
		SystemFn:  func(*Session) string { return strings.Repeat("系统", 100) },
		FileCtxFn: func(*Session) string { return strings.Repeat("检索内容", 150) },
		AllTools:  map[string]contractsToolDef{"inspect_record": {Name: "inspect_record", Description: strings.Repeat("工具定义", 150), InputSchema: json.RawMessage(`{"type":"object"}`)}},
	}
	msgs := requestMessages(cfg, s, cfg.SystemFn(s), false)
	withoutTools := EstimateTokens(msgs)
	if ContextTokens(cfg, s) <= withoutTools || withoutTools <= EstimateTokens(s.Messages) {
		t.Fatal("full request must include system, summary, dynamic context and tool definitions")
	}
	c := fakeLLM(t, []string{sse(`{"choices":[{"delta":{"content":"交接摘要"}}]}`)})
	comp := DefaultCompactor(c, 1000, 2, func(current *Session) int { return ContextTokens(cfg, current) })
	if EstimateTokens(s.Messages) >= 1000 {
		t.Fatal("history alone should be below the trigger")
	}
	if err := comp(context.Background(), s, func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if len(s.Summaries) != 2 || len(s.ArchivedMessages) == 0 {
		t.Fatal("dynamic request budget did not trigger compaction")
	}
}

func TestCompactionPreservesPreparedSystemAndRetrievalOrder(t *testing.T) {
	var delivered string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Messages []llm.Message `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		delivered = body.Messages[0].Content
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte(sse(`{"choices":[{"delta":{"content":"完成"}}]}`)))
	}))
	defer srv.Close()
	cfg := Config{LLM: llm.New(srv.URL, "", "fake"), SystemFn: func(*Session) string { return "base" }, FileCtxFn: func(*Session) string { return "context" }}
	cfg.Hooks.PrepareRequest = func(_ context.Context, s *Session, system *string) error {
		*system += " prepared"
		s.KnowledgeQuery = "fresh retrieval"
		return nil
	}
	cfg.Compact = func(_ context.Context, s *Session, _ func(Event)) error {
		if s.KnowledgeQuery != "fresh retrieval" {
			t.Fatal("compaction ran before retrieval")
		}
		return nil
	}
	s := &Session{}
	if _, err := Run(context.Background(), cfg, s, "analyze", func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if delivered != "base prepared" {
		t.Fatalf("prepared system lost: %q", delivered)
	}
	if len(s.Usage) != 1 || s.Usage[0].InputEstimate == 0 || len(s.Usage[0].VisibleTools) == 0 {
		t.Fatalf("request configuration missing: %+v", s.Usage)
	}
}

func TestEstimateTokensMixedScript(t *testing.T) {
	n := EstimateTokens([]llm.Message{{Role: "user", Content: "你好世界"}, {Role: "assistant", Content: "abcdefgh"}})
	if n != 4+2 {
		t.Fatalf("got %d", n)
	}
}

func TestCompactorKeepsTailAndGroups(t *testing.T) {
	c := fakeLLM(t, []string{sse(`{"choices":[{"delta":{"content":"## 目标\n看 sig_1 的 alpha\n## 已生成产物\nart_aaaaaa"}}]}`)})
	s := &Session{}
	// 12 messages, ends with assistant(tool_calls) + tool pair inside the tail boundary
	for i := 0; i < 5; i++ {
		s.Messages = append(s.Messages, llm.Message{Role: "user", Content: strings.Repeat("问", 300)})
		s.Messages = append(s.Messages, llm.Message{Role: "assistant", Content: strings.Repeat("答", 300)})
	}
	var tc llm.ToolCall
	tc.ID, tc.Function.Name, tc.Function.Arguments = "c1", "plot", "{}"
	s.Messages = append(s.Messages, llm.Message{Role: "assistant", ToolCalls: []llm.ToolCall{tc}})
	s.Messages = append(s.Messages, llm.Message{Role: "tool", ToolCallID: "c1", Name: "plot", Content: "{}"})
	before := len(s.Messages)

	comp := DefaultCompactor(c, 1000, 3, func(current *Session) int { return EstimateTokens(current.Messages) })
	if err := comp(context.Background(), s, func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if len(s.Summaries) != 1 || !strings.Contains(s.Summaries[0], "art_aaaaaa") {
		t.Fatalf("summary missing: %v", s.Summaries)
	}
	if len(s.Messages) >= before || len(s.Messages) < 3 {
		t.Fatalf("tail not kept sensibly: %d -> %d", before, len(s.Messages))
	}
	if len(s.ArchivedMessages)+len(s.Messages) != before {
		t.Fatal("compaction lost original transcript")
	}
	// the cut must not land on a tool message
	if s.Messages[0].Role == "tool" {
		t.Fatalf("cut inside a tool group")
	}
}

func TestSteeringInjectedAfterToolBatch(t *testing.T) {
	c := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"inspect_record","arguments":"{}"}}]}}]}`),
		sse(`{"choices":[{"delta":{"content":"好的。"}}]}`),
	})
	s := &Session{ID: "t"}
	presentation := &llm.InputPresentation{Text: "顺便看看 alpha", References: []llm.InputReference{{WorkspaceID: "ws_123456abcdef", Path: ".", Name: "当前文件夹", Kind: "directory"}}}
	s.Steer("顺便看看 alpha\n<file_references>当前目录</file_references>", presentation)
	cfg := Config{LLM: c, Executor: &fakeExec{}, AllTools: map[string]contractsToolDef{"inspect_record": {Name: "inspect_record"}},
		SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	var types []string
	if _, err := Run(context.Background(), cfg, s, "读文件", func(e Event) { types = append(types, e.Type) }); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, ty := range types {
		if ty == "steering_injected" {
			found = true
		}
	}
	if !found {
		t.Fatalf("steering not injected: %v", types)
	}
	// transcript: user, assistant(tool), tool, user(steer), assistant
	if s.Messages[3].Role != "user" || !strings.Contains(s.Messages[3].Content, "<file_references>") || s.Messages[3].Presentation != presentation {
		t.Fatalf("steer message misplaced: %+v", s.Messages)
	}
}
