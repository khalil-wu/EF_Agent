package contracts

import (
	"os"
	"path/filepath"
	"strings"
)

// RelocateStoragePath maps a path recorded under another machine's root onto root.
func RelocateStoragePath(root, path string) string {
	if path == "" {
		return path
	}
	if _, err := os.Stat(path); err == nil {
		return path
	}
	segs := strings.FieldsFunc(strings.ReplaceAll(path, `\`, "/"), func(r rune) bool { return r == '/' })
	for i := 1; i <= len(segs)-2; i++ {
		candidate := filepath.Join(append([]string{root}, segs[i:]...)...)
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return path
}
