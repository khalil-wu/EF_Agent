package session

import (
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
)

func TestLoadingLegacyTruncatedToolCallsMarksThemWithoutChangingTheirRawInput(t *testing.T) {
	directory := t.TempDir()
	var call llm.ToolCall
	call.ID, call.Type, call.Function.Name = "legacy_call", "function", "run_python"
	call.Function.Arguments = `{"code":"` + strings.Repeat("x", 3177)
	stored := &agent.Session{ID: "ses_abcdef123456", Messages: []llm.Message{{Role: "assistant", ToolCalls: []llm.ToolCall{call}}}, ArchivedMessages: []llm.Message{{Role: "assistant", ToolCalls: []llm.ToolCall{call}}}}
	if err := Save(directory, stored); err != nil {
		t.Fatal(err)
	}
	loaded, err := Load(directory, stored.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, messages := range [][]llm.Message{loaded.Messages, loaded.ArchivedMessages} {
		got := messages[0].ToolCalls[0]
		if got.ArgumentsError == "" || got.Function.Arguments != call.Function.Arguments {
			t.Fatal("legacy raw input was lost or unmarked")
		}
	}
}
