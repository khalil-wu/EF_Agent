package main

import (
	"fmt"
	"sort"
	"strings"
)

func parseAblations(value string) (map[string]bool, error) {
	modes := map[string]bool{}
	for _, raw := range strings.Split(value, ",") {
		name := strings.TrimSpace(raw)
		if name == "" {
			continue
		}
		switch name {
		case "all_tools":
			name = "shared_tools"
		case "no_knowledge":
			name = "no_retrieval"
		case "no_compat":
			name = "no_compat_context"
		case "no_reflect":
			return nil, fmt.Errorf("no_reflect is not an experimental condition: the default runtime has no reflection hook")
		}
		switch name {
		case "shared_tools", "focused_tools", "no_skills", "no_retrieval", "no_requires", "no_compat_context":
			modes[name] = true
		default:
			return nil, fmt.Errorf("unknown EEGAGENT_ABLATION condition: %s", name)
		}
	}
	if modes["shared_tools"] && modes["focused_tools"] {
		return nil, fmt.Errorf("shared_tools and focused_tools select different tool interfaces; choose one")
	}
	return modes, nil
}

func ablationNames(modes map[string]bool) []string {
	var names []string
	for name := range modes {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
