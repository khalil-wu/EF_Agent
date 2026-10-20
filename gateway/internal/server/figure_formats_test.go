package server

import (
	"bytes"
	"encoding/json"
	"mime"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
)

func TestFigureVectorDownloadServesRegisteredRepresentation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "artifacts")
	os.MkdirAll(path, 0o755)
	pdf := []byte{'%', 'P', 'D', 'F', '-', '1', '.', '7', '\n', 0xff, 0x00}
	os.WriteFile(filepath.Join(path, "figure.pdf"), pdf, 0o644)
	os.WriteFile(filepath.Join(path, "figure.png"), []byte("preview"), 0o644)
	rec := map[string]any{"artifact_id": "art_123456", "kind": "plot", "uri": filepath.Join(path, "figure.png"), "media_type": "image/png",
		"representations": map[string]any{"pdf": map[string]string{"uri": filepath.Join(path, "figure.pdf"), "media_type": "application/pdf"}}}
	data, _ := json.Marshal(rec)
	os.WriteFile(filepath.Join(path, "art_123456.json"), data, 0o644)
	handler := New(agent.Config{}, nil, dir).Routes()
	result := request(t, handler, "GET", "/artifacts/art_123456/file?format=pdf&download=1", "")
	if result.Code != 200 || !bytes.Equal(result.Body.Bytes(), pdf) || result.Header().Get("Content-Type") != "application/pdf" || !strings.Contains(result.Header().Get("Content-Disposition"), "attachment") {
		t.Fatal(result.Code, result.Header(), result.Body.Bytes())
	}
	preview := request(t, handler, "GET", "/artifacts/art_123456/file", "")
	if preview.Code != 200 || preview.Body.String() != "preview" {
		t.Fatal(preview.Code, preview.Body.String())
	}
	missing := request(t, handler, "GET", "/artifacts/art_123456/file?format=eps", "")
	if missing.Code != 404 {
		t.Fatal(missing.Code)
	}
}

func TestExportedBinaryReportDownloadIsNotRewrittenAsMarkdown(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "artifacts")
	os.MkdirAll(path, 0o755)
	payload := []byte("%PDF-1.7\nartifact://art_abcdef\xff\x00")
	os.WriteFile(filepath.Join(path, "figure.pdf"), payload, 0o644)
	rec := map[string]string{"artifact_id": "art_abcdef", "kind": "report", "uri": filepath.Join(path, "figure.pdf"), "media_type": "application/pdf"}
	data, _ := json.Marshal(rec)
	os.WriteFile(filepath.Join(path, "art_abcdef.json"), data, 0o644)
	result := request(t, New(agent.Config{}, nil, dir).Routes(), "GET", "/artifacts/art_abcdef/file?download=1", "")
	if result.Code != 200 || !bytes.Equal(result.Body.Bytes(), payload) {
		t.Fatal(result.Code, result.Body.Bytes())
	}
}

func TestKnownICADownloadUsesSemanticChineseNameAndActualTrial(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "artifacts")
	os.MkdirAll(path, 0o755)
	os.MkdirAll(filepath.Join(dir, "manifests"), 0o755)
	os.WriteFile(filepath.Join(dir, "manifests", "sig_fixture.json"), []byte(`{"source_path":"D:/records/s02.dat","trial_index":0}`), 0o644)
	os.WriteFile(filepath.Join(path, "figure.svg"), []byte("<svg/>"), 0o644)
	rec := map[string]any{"artifact_id": "art_123456", "kind": "plot", "uri": filepath.Join(path, "figure.svg"),
		"media_type": "image/svg+xml", "source_manifest_ids": []string{"sig_fixture"},
		"summary": map[string]string{"kind": "clean_ica_iclabel"}}
	data, _ := json.Marshal(rec)
	os.WriteFile(filepath.Join(path, "art_123456.json"), data, 0o644)
	result := request(t, New(agent.Config{}, nil, dir).Routes(), "GET", "/artifacts/art_123456/file?download=1", "")
	_, params, err := mime.ParseMediaType(result.Header().Get("Content-Disposition"))
	if result.Code != 200 || err != nil || params["filename"] != "ICA成分头皮分布_s02_试次1_版本123456.svg" || result.Body.String() != "<svg/>" {
		t.Fatal(result.Code, result.Header(), result.Body.String(), err)
	}
}
