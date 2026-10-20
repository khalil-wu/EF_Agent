package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestStreamSendsOutputBudgetAndPreservesLengthFinishReason(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input request
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
		}
		if input.MaxTokens != 2048 || input.Model != "chosen" {
			t.Errorf("budget=%d model=%s", input.MaxTokens, input.Model)
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"partial\"},\"finish_reason\":\"length\"}],\"usage\":{\"completion_tokens\":2048}}\n\ndata: [DONE]\n\n")
	}))
	defer srv.Close()
	c := New(srv.URL, "", "initial")
	c.MaxTokens = 2048
	out := make(chan Event, 8)
	go c.WithModel("chosen").Stream(context.Background(), nil, nil, out)
	var done Event
	for event := range out {
		if event.Err != nil {
			t.Fatal(event.Err)
		}
		if event.Type == "done" {
			done = event
		}
	}
	if done.FinishReason != "length" || done.Usage == nil || done.Usage.CompletionTokens != 2048 {
		t.Fatal(done)
	}
}

// The first request is hijacked and closed without a response (what a relay dropping a stale
// keep-alive connection looks like); the retry must succeed and the caller sees one clean answer.
func TestStreamRetriesDroppedConnection(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if atomic.AddInt32(&calls, 1) == 1 {
			conn, _, _ := w.(http.Hijacker).Hijack()
			conn.Close()
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer srv.Close()
	c := New(srv.URL, "", "m")
	out := make(chan Event, 16)
	go c.Stream(context.Background(), []Message{{Role: "user", Content: "x"}}, nil, out)
	var text string
	for ev := range out {
		if ev.Type == "error" {
			t.Fatalf("unexpected error: %v", ev.Err)
		}
		text += ev.Text
	}
	if text != "hi" || atomic.LoadInt32(&calls) != 2 {
		t.Fatalf("text=%q calls=%d", text, calls)
	}
}

func TestStreamDoesNotRetryClientErrors(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "bad key", 401)
	}))
	defer srv.Close()
	out := make(chan Event, 4)
	go New(srv.URL, "", "m").Stream(context.Background(), nil, nil, out)
	var got error
	for ev := range out {
		got = ev.Err
	}
	if got == nil || atomic.LoadInt32(&calls) != 1 {
		t.Fatalf("err=%v calls=%d", got, calls)
	}
}

func TestStreamRejectsTruncatedAnswerWithoutReplay(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n")
	}))
	defer srv.Close()
	out := make(chan Event, 8)
	go New(srv.URL, "", "m").Stream(context.Background(), nil, nil, out)
	var got error
	var answer string
	var done bool
	for ev := range out {
		if ev.Err != nil {
			got = ev.Err
		}
		answer += ev.Text
		done = done || ev.Type == "done"
	}
	if got == nil || answer != "partial" || done || calls != 1 {
		t.Fatalf("err=%v answer=%q done=%v calls=%d", got, answer, done, calls)
	}
}

func TestStreamReportsProviderErrorInsideHTTP200(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "data: {\"error\":{\"message\":\"model unavailable\"}}\n\n")
	}))
	defer srv.Close()
	out := make(chan Event, 8)
	go New(srv.URL, "", "m").Stream(context.Background(), nil, nil, out)
	var got error
	for ev := range out {
		if ev.Err != nil {
			got = ev.Err
		}
	}
	if got == nil || got.Error() != "LLM: model unavailable" {
		t.Fatal(got)
	}
}

func TestCloudflareFailureIncludesRequestIDWithoutHTML(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("CF-Ray", "ray-example-MUC")
		w.WriteHeader(522)
		fmt.Fprint(w, "<html>large error document</html>")
	}))
	defer srv.Close()
	out := make(chan Event, 8)
	go New(srv.URL, "", "test").Stream(context.Background(), nil, nil, out)
	var got error
	for ev := range out {
		if ev.Err != nil {
			got = ev.Err
		}
	}
	if got == nil || !strings.Contains(got.Error(), "522") || !strings.Contains(got.Error(), "ray-example-MUC") || strings.Contains(got.Error(), "<html>") {
		t.Fatal(got)
	}
}
