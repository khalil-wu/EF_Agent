package session

import (
	"os"
	"path/filepath"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
)

func TestLoadRelocatesManifestPathsFromAnotherMachine(t *testing.T) {
	dir := t.TempDir()
	upload := filepath.Join(dir, "uploads", "rec.edf")
	if err := os.MkdirAll(filepath.Dir(upload), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(upload, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	s := &agent.Session{ID: "ses_abcdef123456", Manifests: []*contracts.Manifest{
		{ManifestID: "sig_live", Modality: "eeg", SourcePath: `E:\EF_Agent\storage\uploads\rec.edf`, SourceFiles: []string{`E:\EF_Agent\storage\uploads\rec.edf`}},
	}, StagedManifests: []*contracts.Manifest{
		{ManifestID: "img_live", Modality: "image", VisionPath: `E:\EF_Agent\storage\uploads\rec.edf`},
	}}
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	loaded, err := Load(dir, s.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Manifests[0].SourcePath != upload || loaded.Manifests[0].SourceFiles[0] != upload {
		t.Fatalf("manifest paths not relocated: %+v", loaded.Manifests[0])
	}
	if loaded.StagedManifests[0].VisionPath != upload {
		t.Fatalf("staged vision path not relocated: %+v", loaded.StagedManifests[0])
	}
}
