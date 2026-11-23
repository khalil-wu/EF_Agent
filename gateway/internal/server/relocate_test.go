package server

import (
	"os"
	"path/filepath"
	"testing"

	"eegagent/gateway/internal/agent"
)

func TestArtifactFileServesRecordsWrittenUnderAnotherMachineRoot(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "artifacts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "artifacts", "art_1234567890ab.png"), []byte("png"), 0o644); err != nil {
		t.Fatal(err)
	}
	record := `{"artifact_id":"art_1234567890ab","kind":"plot","uri":"E:\\EF_Agent\\storage\\artifacts\\art_1234567890ab.png","media_type":"image/png","provenance":{"steps":[]}}`
	if err := os.WriteFile(filepath.Join(dir, "artifacts", "art_1234567890ab.json"), []byte(record), 0o644); err != nil {
		t.Fatal(err)
	}
	h := New(agent.Config{}, nil, dir).Routes()
	w := request(t, h, "GET", "/artifacts/art_1234567890ab/file", "")
	if w.Code != 200 || w.Body.String() != "png" {
		t.Fatalf("artifact file: %d %s", w.Code, w.Body.String())
	}
}
