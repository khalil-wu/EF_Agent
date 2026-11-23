package agent

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"eegagent/gateway/internal/llm"
)

// Timestamps are persisted for UI timing but must never reach the provider request.
func TestMessageTimestampsPersistAndStayOutOfProviderRequests(t *testing.T) {
	var mu sync.Mutex
	var bodies []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		mu.Lock()
		bodies = append(bodies, string(raw))
		mu.Unlock()
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte(sse(`{"choices":[{"delta":{"content":"Done."}}]}`)))
	}))
	t.Cleanup(srv.Close)

	s := &Session{ID: "ses_ts"}
	cfg := Config{LLM: llm.New(srv.URL, "", "fake"), SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	if _, err := Run(context.Background(), cfg, s, "hello", func(Event) {}); err != nil {
		t.Fatal(err)
	}
	s.Steer("more", nil)

	var stamped int
	for _, message := range s.Messages {
		switch message.Role {
		case "user", "assistant":
			if message.Timestamp == nil {
				t.Fatalf("%s message missing timestamp: %+v", message.Role, message)
			}
			stamped++
		case "tool":
			if message.Timestamp != nil {
				t.Fatalf("tool message should stay unstamped: %+v", message)
			}
		}
	}
	if stamped < 2 {
		t.Fatalf("expected user and assistant messages: %+v", s.Messages)
	}
	for _, m := range s.steer {
		if m.Timestamp == nil {
			t.Fatalf("steered message missing timestamp: %+v", m)
		}
	}

	mu.Lock()
	defer mu.Unlock()
	if len(bodies) == 0 {
		t.Fatal("no provider request captured")
	}
	for _, body := range bodies {
		if strings.Contains(body, `"ts"`) {
			t.Fatalf("provider request leaked a timestamp: %s", body)
		}
	}
}
