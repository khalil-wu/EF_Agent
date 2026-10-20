package contracts

import (
	"path/filepath"
	"strings"
)

type FileMove struct {
	Source      string `json:"source"`
	Destination string `json:"destination"`
}

// Keep registered references pointing to the same data after workspace organization.
func UpdatePaths(manifests []*Manifest, moves []FileMove) bool {
	changed := false
	for _, move := range moves {
		adjust := func(value string) string {
			relative, err := filepath.Rel(move.Source, value)
			if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
				return value
			}
			return filepath.Join(move.Destination, relative)
		}
		for _, manifest := range manifests {
			next := adjust(manifest.SourcePath)
			if next != manifest.SourcePath {
				changed = true
				manifest.SourcePath = next
			}
			for i, path := range manifest.SourceFiles {
				next := adjust(path)
				if next != path {
					changed = true
					manifest.SourceFiles[i] = next
				}
			}
		}
	}
	return changed
}
