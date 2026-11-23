package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
)

func TestRunTimeoutReportsAReadableErrorAndKeepsRetryAvailable(t *testing.T) {
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer model.Close()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	s := New(cfg, nil, t.TempDir())
	s.RunTimeout = 50 * time.Millisecond
	h := s.Routes()
	id := create(t, h)
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/sessions/"+id+"/messages", strings.NewReader(`{"text":"hello"}`)))
		done <- w
	}()
	var w *httptest.ResponseRecorder
	select {
	case w = <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("message handler did not finish after the run timeout")
	}
	body := w.Body.String()
	if strings.Count(body, "event: error") != 1 || !strings.Contains(body, "运行上限") || !strings.Contains(body, "event: session_saved") {
		t.Fatal(body)
	}
	view := request(t, h, "GET", "/sessions/"+id, "")
	if !strings.Contains(view.Body.String(), "运行上限") {
		t.Fatal("last_error must hold the readable timeout message:", view.Body.String())
	}
}
