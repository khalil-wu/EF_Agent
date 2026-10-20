package main

import (
	"reflect"
	"strings"
	"testing"
)

func TestAblationsCanonicalControlsAndAliases(t *testing.T) {
	modes, err := parseAblations(" all_tools,no_knowledge,no_compat,no_skills,no_requires,all_tools ")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"no_compat_context", "no_requires", "no_retrieval", "no_skills", "shared_tools"}
	if got := ablationNames(modes); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
	baseline, err := parseAblations("")
	if err != nil || len(baseline) != 0 {
		t.Fatalf("default should retain production configuration: %v %v", baseline, err)
	}
	if _, err := parseAblations("focused_tools"); err != nil {
		t.Fatal(err)
	}
}

func TestAblationsRejectNonexistentAndContradictoryInterventions(t *testing.T) {
	for _, test := range []struct{ value, reason string }{
		{"no_reflect", "no reflection hook"},
		{"made_up", "unknown"},
		{"shared_tools,focused_tools", "different tool interfaces"},
	} {
		_, err := parseAblations(test.value)
		if err == nil || !strings.Contains(err.Error(), test.reason) {
			t.Fatalf("%s: %v", test.value, err)
		}
	}
}
