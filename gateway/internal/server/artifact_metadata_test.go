package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"eegagent/gateway/internal/agent"
)

func TestArtifactMetadataPreservesSignalChannelsRateAndOffset(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "artifacts"), 0o755); err != nil {
		t.Fatal(err)
	}
	body := `{"artifact_id":"art_abcdef","kind":"normalized_signal","uri":"source.npy","source_manifest_ids":["sig_abcdef"],"signal":{"sample_rate_hz":250,"channel_names":["O1","O2"],"channel_types":["eeg","eeg"],"time_offset_s":3,"amplitude_unit":"µV"}}`
	if err := os.WriteFile(filepath.Join(dir, "artifacts", "art_abcdef.json"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	s := New(agent.Config{}, nil, dir)
	w := httptest.NewRecorder()
	s.Routes().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/artifacts/art_abcdef", nil))
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	var result struct {
		Signal struct {
			Rate   float64  `json:"sample_rate_hz"`
			Names  []string `json:"channel_names"`
			Offset float64  `json:"time_offset_s"`
		} `json:"signal"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Signal.Rate != 250 || result.Signal.Offset != 3 || len(result.Signal.Names) != 2 || result.Signal.Names[0] != "O1" {
		t.Fatalf("signal metadata lost through HTTP: %s", w.Body.String())
	}
}
