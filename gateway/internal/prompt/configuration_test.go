package prompt

import (
	"encoding/json"
	"strings"
	"testing"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/skills"
)

func TestNoSkillsPrefixOmitsProceduralIndexAndReadInterface(t *testing.T) {
	base := StaticPrefix(PrefixOptions{Mode: contracts.ModeReadonly})
	if strings.Contains(base, "read_skill") || strings.Contains(base, "available_skills") {
		t.Fatal("no-skills prefix still advertises skills")
	}
	withSkills := StaticPrefix(PrefixOptions{Mode: contracts.ModeReadonly, Skills: []*skills.Skill{{Name: "eeg", Description: "EEG methods"}}})
	if !strings.Contains(withSkills, "available_skills") || !strings.Contains(withSkills, SkillGuidance) {
		t.Fatal("enabled skills lost index or reading guidance")
	}
}

func TestEpochManifestProjectionRetainsEventCoordinates(t *testing.T) {
	var manifest contracts.Manifest
	err := json.Unmarshal([]byte(`{"manifest_id":"sig_abcdef","modality":"eeg","signal_kind":"epochs","n_segments":120,"time_axis":"packed epochs; boundaries are discontinuous","epoch_tmin_range_s":[-0.2,-0.2],"segments_preview":[{"epoch_index":0,"epoch_tmin_s":-0.2,"event_zero_sample":50}],"n_channels":2,"channel_names":["C3","C4"]}`), &manifest)
	if err != nil {
		t.Fatal(err)
	}
	context := FileContext([]*contracts.Manifest{&manifest})
	for _, expected := range []string{"signal_kind=epochs", "n_segments=120", "epoch_tmin_range_s=[-0.2 -0.2]", `"event_zero_sample":50`, "boundaries are discontinuous", "C3,C4"} {
		if !strings.Contains(context, expected) {
			t.Fatalf("lost %s: %s", expected, context)
		}
	}
}

func TestSelectedTrialProjectionReportsTheActualFileScope(t *testing.T) {
	index := 1
	m := &contracts.Manifest{ManifestID: "sig_trial", Modality: "eeg", TrialIndex: &index, TrialCount: 40, TrialSelection: "explicit"}
	text := FileContext([]*contracts.Manifest{m})
	if !strings.Contains(text, "file_trial_count=40 selected_trial_index=1") || !strings.Contains(text, "当前记录只包含这个 trial") {
		t.Fatal(text)
	}
}

func TestInitialQualitySampleDoesNotReplaceRecordingCoverage(t *testing.T) {
	rate, duration := 250.0, 60.0
	m := &contracts.Manifest{ManifestID: "sig_full", Modality: "eeg", SampleRateHz: &rate, DurationS: &duration,
		Quality: json.RawMessage(`{"scope":"initial_sample","sample_count":2500,"sample_time_range_s":[0,10]}`)}
	text := FileContext([]*contracts.Manifest{m})
	if !strings.Contains(text, "duration_s=60") || !strings.Contains(text, "初筛样本数不是有效数据总量") {
		t.Fatal(text)
	}
}
