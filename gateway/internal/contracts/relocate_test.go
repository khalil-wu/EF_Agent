package contracts

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRelocateStoragePath(t *testing.T) {
	root := t.TempDir()
	for _, dir := range []string{"artifacts", "storage/uploads/s/u"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	plot := filepath.Join(root, "artifacts", "a.png")
	live := filepath.Join(root, "storage", "uploads", "s", "u", "f.edf")
	stray := filepath.Join(root, "f.edf")
	for _, path := range []string{plot, live, stray} {
		if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cases := map[string]string{
		"":                                    "",
		plot:                                  plot,
		`E:\EF_Agent\storage\artifacts\a.png`: plot,
		"/old/root/storage/uploads/s/u/f.edf": live,
		`E:\nowhere\else\gone.png`:            `E:\nowhere\else\gone.png`,
		`E:\EF_Agent\f.edf`:                   `E:\EF_Agent\f.edf`, // a bare file-name match is not enough
	}
	for in, want := range cases {
		if got := RelocateStoragePath(root, in); got != want {
			t.Errorf("RelocateStoragePath(%q) = %q, want %q", in, got, want)
		}
	}
}
