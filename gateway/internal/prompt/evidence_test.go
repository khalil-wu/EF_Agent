package prompt

import (
	"encoding/json"
	"strings"
	"testing"

	"eegagent/gateway/internal/contracts"
)

func TestFileContextPreservesEvidenceWithoutPromotingFormatAssumptions(t *testing.T) {
	var manifest contracts.Manifest
	err := json.Unmarshal([]byte(`{"manifest_id":"sig_shape","modality":"eeg","dataset":"unknown","format_profile":"deap_preprocessed","sample_rate_hz":null,"duration_s":null,"channel_names":["ch1"],"labels":{"values":[6,4,3,7]},"metadata_evidence":{"origin":{"value":"unverified","source":"unknown"},"sample_rate_hz":{"value":128,"source":"format_assumption"}},"unit":{"value":"uV","source":"inferred"}}`), &manifest)
	if err != nil {
		t.Fatal(err)
	}
	context := FileContext([]*contracts.Manifest{&manifest})
	for _, want := range []string{"dataset=unknown", "format_profile=deap_preprocessed", "sample_rate_hz=unknown", `"value":128,"source":"format_assumption"`, `"origin":{"value":"unverified","source":"unknown"}`, "labels_available=true", "unit=uV unit_source=inferred"} {
		if !strings.Contains(context, want) {
			t.Errorf("lost evidence %q in %s", want, context)
		}
	}
	if strings.Contains(context, `"values":[6,4,3,7]`) || strings.Contains(context, "labels={") {
		t.Fatalf("self-report values leaked into prediction context: %s", context)
	}
}

func TestLegacyDatasetNameDoesNotBecomeSourceProof(t *testing.T) {
	manifest := &contracts.Manifest{ManifestID: "sig_old", Modality: "eeg", Dataset: "DEAP",
		ChannelLayout: &contracts.Inferred{Source: "dataset_adapter"}}
	context := FileContext([]*contracts.Manifest{manifest})
	if !strings.Contains(context, "metadata_evidence=unavailable") || !strings.Contains(context, "channel_layout.source=dataset_adapter") {
		t.Fatalf("legacy evidence warning missing: %s", context)
	}
}

func TestFileContextKeepsLabelAvailabilityAfterValuesAreHidden(t *testing.T) {
	var manifest contracts.Manifest
	if err := json.Unmarshal([]byte(`{"manifest_id":"sig_labels","modality":"eeg","labels_available":true}`), &manifest); err != nil {
		t.Fatal(err)
	}
	context := FileContext([]*contracts.Manifest{&manifest})
	if !strings.Contains(context, "labels_available=true") || strings.Contains(context, "labels={") {
		t.Fatalf("hidden-label availability lost or values exposed: %s", context)
	}
}

func TestKnowledgeContextKeepsUntranscribedFormulaScope(t *testing.T) {
	raw := json.RawMessage(`{"chunk_id":"k_abcdef","doc_id":"paper_math","page":2,"text":"A Gaussian assumption is used.","source_kind":"paper_excerpt","parse_note":"该页含未转写的数学公式，相关公式须查看原 PDF。"}`)
	context := KnowledgeContext([]json.RawMessage{raw}, nil)
	if !strings.Contains(context, `page=2`) || !strings.Contains(context, "未转写的数学公式") {
		t.Fatalf("missing formula scope in model context: %s", context)
	}
}

func TestPairedVideoSyncIsAvailableToModel(t *testing.T) {
	var manifest contracts.Manifest
	if err := json.Unmarshal([]byte(`{"manifest_id":"vid_pair","modality":"video","role":{"value":"face","source":"user_confirmed"},"sync":{"clock":"eeg_original","start_offset_s":20,"source":"user_confirmed"}}`), &manifest); err != nil {
		t.Fatal(err)
	}
	context := FileContext([]*contracts.Manifest{&manifest})
	if !strings.Contains(context, `"start_offset_s":20`) || !strings.Contains(context, "original EEG time") || !strings.Contains(context, "role=face") {
		t.Fatalf("lost actual pair evidence: %s", context)
	}
}
