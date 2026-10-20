package llm

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestPresentationIsSavedOnMessagesButNotSentToProvider(t *testing.T) {
	var requestBody string
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		requestBody = string(body)
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"OK\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer provider.Close()
	client := New(provider.URL, "", "model")
	message := Message{MessageID: "msg_123", Role: "user", Content: "完整的问题与回答", Presentation: &InputPresentation{Text: "简短的回答", Questions: []string{"问题"}}, Phase: "final_answer", CitationIDs: []string{"k_abcdef"}}
	out := make(chan Event, 16)
	go client.Stream(context.Background(), []Message{message}, nil, out)
	for event := range out {
		if event.Err != nil {
			t.Fatal(event.Err)
		}
	}
	if strings.Contains(requestBody, "message_id") || strings.Contains(requestBody, "presentation") || strings.Contains(requestBody, "phase") || strings.Contains(requestBody, "citation_ids") || strings.Contains(requestBody, "简短的回答") || !strings.Contains(requestBody, "完整的问题与回答") {
		t.Fatal(requestBody)
	}
	if message.MessageID != "msg_123" || message.Presentation == nil || message.Phase != "final_answer" || len(message.CitationIDs) != 1 {
		t.Fatal("provider serialization mutated saved transcript")
	}
}
