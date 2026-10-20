package agent

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"eegagent/gateway/internal/llm"
)

func TestRestoreGraphEvidenceFromArchivedTranscript(t *testing.T) {
	s := &Session{ArchivedMessages: []llm.Message{{Role: "tool", Name: "knowledge_graph", Content: `{"path":[{"evidence":["k_abcdef"]}],"chunks":[{"chunk_id":"k_abcdef","text":"historical source","page":4}]}`}}}
	s.RestoreSources()
	if !strings.Contains(string(s.Sources["k_abcdef"]), "historical source") {
		t.Fatal("graph source was not recovered")
	}
}

func TestSourceSnapshotsKeepFirstExcerptAndOwnTheirBytes(t *testing.T) {
	s := &Session{}
	first := json.RawMessage(`{"chunk_id":"k_abcdef123456","text":"original excerpt","page":2}`)
	s.RememberSources([]json.RawMessage{first})
	s.RememberSources([]json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef123456","text":"changed index"}`)})
	first[0] = ' '
	if string(s.Sources["k_abcdef123456"]) != `{"chunk_id":"k_abcdef123456","text":"original excerpt","page":2}` {
		t.Fatalf("the first citation excerpt changed: %s", s.Sources["k_abcdef123456"])
	}
}

func TestAssistantCitationIDsUseKnownEvidenceAndSkipCode(t *testing.T) {
	s := &Session{}
	s.RememberSources([]json.RawMessage{
		json.RawMessage(`{"chunk_id":"k_abcdef","text":"source A"}`),
		json.RawMessage(`{"chunk_id":"k_123456","text":"source B"}`),
	})
	m := s.assistantMessage("One [k_abcdef], repeated [k_abcdef], unknown [k_999999]. `k_123456`\n```\n[k_123456]\n```", "final_answer")
	if m.Phase != "final_answer" || !reflect.DeepEqual(m.CitationIDs, []string{"k_abcdef"}) {
		t.Fatalf("unexpected assistant metadata: %+v", m)
	}
}

func TestCompletionEventsMatchSavedAssistantMessages(t *testing.T) {
	client := fakeLLM(t, []string{
		sse(`{"choices":[{"delta":{"content":"Source [k_abcdef].","tool_calls":[{"index":0,"id":"c1","function":{"name":"inspect_record","arguments":"{}"}}]}}]}`),
		sse(`{"choices":[{"delta":{"content":"Answer [k_abcdef]."}}]}`),
	})
	s := &Session{ID: "ses_abcdef"}
	s.RememberSources([]json.RawMessage{json.RawMessage(`{"chunk_id":"k_abcdef","text":"reference"}`)})
	cfg := Config{LLM: client, Executor: &fakeExec{}, SystemFn: func(*Session) string { return "SYS" }, FileCtxFn: func(*Session) string { return "CTX" }}
	var completed []Event
	_, err := Run(context.Background(), cfg, s, "analyse", func(event Event) {
		if event.Type == "message_completed" {
			completed = append(completed, event)
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(completed) != 2 || completed[0].Phase != "commentary" || completed[1].Phase != "final_answer" {
		t.Fatalf("unexpected completion events: %+v", completed)
	}
	for i, messageIndex := range []int{1, 3} {
		message := s.Messages[messageIndex]
		if message.MessageID == "" || message.MessageID != completed[i].MessageID || message.Phase != completed[i].Phase || !reflect.DeepEqual(message.CitationIDs, completed[i].CitationIDs) || len(message.CitationIDs) != 1 {
			t.Fatalf("live completion and replay diverged: %+v %+v", completed[i], message)
		}
	}
}
