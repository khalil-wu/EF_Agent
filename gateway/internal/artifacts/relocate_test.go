package artifacts

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadRelocatesWindowsRecordedPaths(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "artifacts"), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"art_1234567890ab.png", "art_1234567890ab.svg"} {
		if err := os.WriteFile(filepath.Join(dir, "artifacts", name), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	record := `{"artifact_id":"art_1234567890ab","kind":"plot","uri":"E:\\EF_Agent\\storage\\artifacts\\art_1234567890ab.png","media_type":"image/png","representations":{"svg":{"uri":"E:\\EF_Agent\\storage\\artifacts\\art_1234567890ab.svg","media_type":"image/svg+xml"}},"provenance":{"steps":[]}}`
	if err := os.WriteFile(filepath.Join(dir, "artifacts", "art_1234567890ab.json"), []byte(record), 0o644); err != nil {
		t.Fatal(err)
	}
	rec, err := Load(dir, "art_1234567890ab")
	if err != nil {
		t.Fatal(err)
	}
	if rec.URI != filepath.Join(dir, "artifacts", "art_1234567890ab.png") {
		t.Fatal(rec.URI)
	}
	if rec.Representations["svg"].URI != filepath.Join(dir, "artifacts", "art_1234567890ab.svg") {
		t.Fatal(rec.Representations["svg"].URI)
	}
}
