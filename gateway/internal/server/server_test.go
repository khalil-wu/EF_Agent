package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSafeNameKeepsOriginalNames(t *testing.T) {
	cases := map[string]string{
		"sub-01_task-rest_eeg.vhdr": "sub-01_task-rest_eeg.vhdr",
		"my data.set":               "my data.set",
		`C:\Users\x\rec.edf`:        "rec.edf",
		"../../etc/passwd":          "passwd",
		"a<b>:c?.fdt":               "a_b__c_.fdt",
		"..":                        "file",
		"情绪实验 01.bdf":               "情绪实验 01.bdf",
	}
	for in, want := range cases {
		if got := safeName(in); got != want {
			t.Errorf("safeName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestStoreUploadDedupesAndKeepsSidecarNames(t *testing.T) {
	dir := t.TempDir()
	p1, err := storeUpload(dir, "rec.set", strings.NewReader("header"))
	if err != nil || filepath.Base(p1) != "rec.set" {
		t.Fatalf("first upload: %v %s", err, p1)
	}
	p2, _ := storeUpload(dir, "rec.set", strings.NewReader("header"))
	if p2 != p1 {
		t.Fatalf("identical upload should reuse %s, got %s", p1, p2)
	}
	if _, err := storeUpload(dir, "rec.set", strings.NewReader("different")); err == nil {
		t.Fatal("one upload group accepted different content under the same member name")
	}
	p4, _ := storeUpload(dir, "rec.fdt", strings.NewReader("data"))
	if filepath.Base(p4) != "rec.fdt" {
		t.Fatalf("sidecar must keep its name, got %s", p4)
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".upload-") {
			t.Fatalf("temp file left behind: %s", e.Name())
		}
	}
}

func TestExportedReportLinksOpenInLocalService(t *testing.T) {
	got := localArtifactLinks("![功率谱](artifact://art_abcdef123) 和 artifact://art_123456abc", "localhost:8080")
	if !strings.Contains(got, "![功率谱](http://localhost:8080/artifacts/art_abcdef123/file)") || !strings.Contains(got, "http://localhost:8080/artifacts/art_123456abc/file") {
		t.Fatal(got)
	}
}
