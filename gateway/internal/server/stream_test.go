package server

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
)

func TestEventEncodingDoesNotWriteAnEmptyDataRecordOnInvalidJSON(t *testing.T) {
	response := httptest.NewRecorder()
	_, err := writeEvent(response, agent.Event{Type: "tool_execution_start", Args: json.RawMessage(`{"code":"unfinished`)})
	if err == nil || response.Body.Len() != 0 {
		t.Fatal("invalid event was written as a broken SSE record", err, response.Body.String())
	}
}

func TestLiveResumeIncludesPartialAndReplaysEveryEventAfterTheSnapshot(t *testing.T) {
	middle, finish := make(chan struct{}), make(chan struct{})
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"reasoning_content\":\"PROVIDER_REVIEW\",\"content\":\"alpha\"}}]}\n\n")
		w.(http.Flusher).Flush()
		select {
		case <-middle:
		case <-r.Context().Done():
			return
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"beta\"}}]}\n\n")
		w.(http.Flusher).Flush()
		select {
		case <-finish:
		case <-r.Context().Done():
			return
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"gamma\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer model.Close()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), SystemFn: func(*agent.Session) string { return "SYS" }, FileCtxFn: func(*agent.Session) string { return "CTX" }}
	s := New(cfg, nil, t.TempDir())
	h := s.Routes()
	server := httptest.NewServer(h)
	defer server.Close()
	id := create(t, h)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	delta := make(chan struct{}, 4)
	postDone := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		w := &observedResponse{ResponseRecorder: httptest.NewRecorder(), delta: delta}
		h.ServeHTTP(w, httptest.NewRequest("POST", "/sessions/"+id+"/messages", strings.NewReader(`{"text":"hello"}`)))
		postDone <- w.ResponseRecorder
	}()
	defer s.stop(httptest.NewRecorder(), httptest.NewRequest("POST", "/sessions/"+id+"/stop", nil))
	waitDelta := func() {
		t.Helper()
		select {
		case <-delta:
		case <-time.After(5 * time.Second):
			t.Fatal("public token was not delivered before model completion")
		}
	}
	waitDelta()
	var snapshot struct {
		Running bool `json:"running"`
		Live    struct {
			RunID    string        `json:"run_id"`
			Sequence uint64        `json:"sequence"`
			Events   []agent.Event `json:"events"`
		} `json:"live"`
	}
	view := request(t, h, "GET", "/sessions/"+id, "")
	if err := json.Unmarshal(view.Body.Bytes(), &snapshot); err != nil {
		t.Fatal(err)
	}
	partial := ""
	reasoning := ""
	messageID := ""
	for _, event := range snapshot.Live.Events {
		if event.Type == "reasoning_delta" {
			reasoning += event.Text
		}
		if event.Type == "message_delta" {
			partial += event.Text
			messageID = event.MessageID
		}
	}
	if !snapshot.Running || partial != "alpha" || messageID == "" || reasoning != "PROVIDER_REVIEW" {
		t.Fatalf("snapshot did not preserve separate reasoning and answer streams: %s", view.Body.String())
	}
	// The next token intentionally lands after the snapshot and before subscription.
	close(middle)
	waitDelta()
	url := fmt.Sprintf("%s/sessions/%s/events?run_id=%s&after=%d", server.URL, id, snapshot.Live.RunID, snapshot.Live.Sequence)
	req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal(response.Status)
	}
	close(finish)
	sequence, suffix, completed := snapshot.Live.Sequence, "", false
	scanner := bufio.NewScanner(response.Body)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var event agent.Event
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event); err != nil {
			t.Fatal(err)
		}
		if event.RunID != snapshot.Live.RunID || event.Sequence != sequence+1 {
			t.Fatalf("resume skipped or repeated a public event: previous=%d event=%+v", sequence, event)
		}
		sequence = event.Sequence
		if event.Type == "message_delta" {
			if event.MessageID != messageID {
				t.Fatalf("resume changed the message identity: %+v", event)
			}
			suffix += event.Text
		}
		if event.Type == "message_completed" {
			completed = event.MessageID == messageID && event.Phase == "final_answer"
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if suffix != "betagamma" || !completed {
		t.Fatalf("partial replay duplicated/lost text or completion: suffix=%q completed=%v", suffix, completed)
	}
	select {
	case post := <-postDone:
		if !strings.Contains(post.Body.String(), "PROVIDER_REVIEW") {
			t.Fatal("provider reasoning did not reach the original stream")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("model request did not finish")
	}
	if final := request(t, h, "GET", "/sessions/"+id, ""); strings.Contains(final.Body.String(), "PROVIDER_REVIEW") {
		t.Fatal("completed history retained expired reasoning")
	}
	if stale := request(t, h, "GET", "/sessions/"+id+"/events?run_id=run_previous&after=0", ""); stale.Code != 409 {
		t.Fatal("a different run accepted the old cursor")
	}
}
