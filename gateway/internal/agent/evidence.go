package agent

import (
	"encoding/json"
	"regexp"

	"eegagent/gateway/internal/llm"
)

var citationID = regexp.MustCompile(`\bk_[0-9a-f]{6,}\b`)
var sourceID = regexp.MustCompile(`^k_[0-9a-f]{6,}$`)
var quotedCode = regexp.MustCompile("(?s)```.*?```|`[^`]*`")

func (s *Session) RememberSources(chunks []json.RawMessage) {
	if s.Sources == nil {
		s.Sources = make(map[string]json.RawMessage)
	}
	for _, raw := range chunks {
		var source struct {
			ID   string `json:"chunk_id"`
			Text string `json:"text"`
		}
		if json.Unmarshal(raw, &source) != nil || source.Text == "" || !sourceID.MatchString(source.ID) {
			continue
		}
		if _, exists := s.Sources[source.ID]; !exists {
			s.Sources[source.ID] = append(json.RawMessage(nil), raw...)
		}
	}
}

// Recover evidence already present in older transcripts; never replace it with today's index.
func (s *Session) RestoreSources() {
	for _, messages := range [][]llm.Message{s.ArchivedMessages, s.Messages} {
		for _, message := range messages {
			if message.Role != "tool" || (message.Name != "search_knowledge" && message.Name != "read_document" && message.Name != "knowledge_graph") {
				continue
			}
			var result struct {
				Chunks []json.RawMessage `json:"chunks"`
			}
			if json.Unmarshal([]byte(message.Content), &result) == nil {
				s.RememberSources(result.Chunks)
			}
		}
	}
	s.RememberSources(s.Knowledge)
}

func (s *Session) assistantMessage(text, phase string) llm.Message {
	message := llm.Message{Role: "assistant", Content: text, Phase: phase}
	seen := map[string]bool{}
	for _, id := range citationID.FindAllString(quotedCode.ReplaceAllString(text, ""), -1) {
		if _, available := s.Sources[id]; available && !seen[id] {
			message.CitationIDs = append(message.CitationIDs, id)
			seen[id] = true
		}
	}
	return message
}
