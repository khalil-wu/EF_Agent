package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"eegagent/gateway/internal/agent"
)

// The current run's public events are retained until the next run starts. This makes a
// snapshot's sequence an exact boundary for a refresh or a return to a running chat.
type liveRun struct {
	id        string
	startedAt time.Time
	events    []agent.Event
	base      int
	done      bool
	changed   chan struct{}
}

func writeEvent(w http.ResponseWriter, event agent.Event) (int, error) {
	body, err := json.Marshal(event)
	if err != nil {
		return 0, fmt.Errorf("encode %s event: %w", event.Type, err)
	}
	return fmt.Fprintf(w, "id: %s:%d\nevent: %s\ndata: %s\n\n", event.RunID, event.Sequence, event.Type, body)
}

func (s *Server) events(w http.ResponseWriter, r *http.Request) {
	ses := s.get(r.PathValue("id"))
	if ses == nil {
		writeJSON(w, 404, map[string]string{"error": "no such session"})
		return
	}
	after := uint64(0)
	if value := r.URL.Query().Get("after"); value != "" {
		var err error
		after, err = strconv.ParseUint(value, 10, 64)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "after must be an event sequence"})
			return
		}
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, 500, map[string]string{"error": "streaming unsupported"})
		return
	}
	s.mu.Lock()
	run := s.live[ses.ID]
	if run == nil || run.id != r.URL.Query().Get("run_id") || after > uint64(len(run.events)) {
		s.mu.Unlock()
		writeJSON(w, 409, map[string]string{"error": "当前生成已变化，请重新读取会话"})
		return
	}
	s.mu.Unlock()
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(200)
	flusher.Flush()
	for {
		s.mu.Lock()
		pending := append([]agent.Event(nil), run.events[int(after):]...)
		changed, done := run.changed, run.done
		s.mu.Unlock()
		for _, event := range pending {
			if _, err := writeEvent(w, event); err != nil {
				return
			}
			after = event.Sequence
			flusher.Flush()
		}
		if done {
			return
		}
		select {
		case <-r.Context().Done():
			return
		case <-changed:
		}
	}
}
