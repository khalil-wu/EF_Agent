package hooks

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
)

func TestRecognitionUpdatesBothSourcesWithoutDuplicatingLargeMetadata(t *testing.T) {
	s := &agent.Session{Manifests: []*contracts.Manifest{
		{ManifestID: "sig_old", Modality: "eeg", AttachmentSHA256: "eeg", OriginalName: "brain.dat"},
		{ManifestID: "vid_old", Modality: "video", AttachmentSHA256: "video", OriginalName: "face.avi"}}}
	body := marshalForTest(t, map[string]any{"status": "ok", "prediction_executed": true, "manifest_id": "sig_new", "video_manifest_id": "vid_new",
		"manifests": []map[string]any{
			{"manifest_id": "sig_new", "modality": "eeg", "attachment_sha256": "eeg", "metadata_evidence": map[string]any{"note": strings.Repeat("explicit input fact", 1000)}},
			{"manifest_id": "vid_new", "modality": "video", "attachment_sha256": "video", "sync": map[string]any{"start_offset_s": 3, "source": "user_confirmed"}}},
		"summary": map[string]any{"prediction_count": 60, "label_counts": map[string]int{"HVLA": 58, "LVHA": 2}}})
	h := Build(nil, t.TempDir(), nil)
	result, failed := h.AfterTool(context.Background(), s, agent.ToolCall{Name: "recognize_emotion"}, body, false)
	if failed || len(s.Manifests) != 2 || s.Manifests[0].ManifestID != "sig_new" || s.Manifests[1].ManifestID != "vid_new" || s.Manifests[0].OriginalName != "brain.dat" || s.Manifests[1].OriginalName != "face.avi" {
		t.Fatal("updated sources lost")
	}
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(result, &fields)
	if _, exists := fields["manifests"]; exists || len(result) > maxToolResultChars || !strings.Contains(string(fields["summary"]), `"HVLA":58`) {
		t.Fatal("result repeats metadata or loses full counts", string(result))
	}
}

func TestLocalAnalysisKeepsMeasuredSummaryAndFullStoredArtifacts(t *testing.T) {
	s := &agent.Session{}
	body := marshalForTest(t, map[string]any{"status": "ok", "manifest": map[string]any{"manifest_id": "sig_123456", "modality": "eeg", "attachment_sha256": "data", "compat": []map[string]any{{"model_id": "test", "reasons": []string{strings.Repeat("catalog", 2000)}}}},
		"artifacts":    []map[string]any{{"artifact_id": "art_123456", "kind": "features", "summary": map[string]any{"relative_bandpower": map[string]float64{"alpha": 0.9}, "measurement_settings": strings.Repeat("settings", 1000)}}},
		"summary":      map[string]any{"n_windows": 4, "prediction_executed": false, "features": map[string]any{"relative_bandpower": map[string]float64{"alpha": 0.9}, "power_unit": "uV²"}},
		"result_links": []map[string]string{{"artifact_id": "art_123456", "download_url": "/artifacts/art_123456/file"}}})
	h := Build(nil, t.TempDir(), nil)
	result, failed := h.AfterTool(context.Background(), s, agent.ToolCall{Name: "analyze_signal"}, body, false)
	var fields map[string]json.RawMessage
	json.Unmarshal(result, &fields)
	if failed || len(result) > maxToolResultChars || len(fields["manifest"]) > 0 || len(fields["artifacts"]) > 0 || !strings.Contains(string(fields["summary"]), `"alpha":0.9`) || len(fields["result_links"]) == 0 {
		t.Fatalf("local summary or links lost: %s", result)
	}
	if len(s.Manifests) != 1 || len(s.Artifacts) != 1 || !strings.Contains(string(s.Artifacts[0]), strings.Repeat("settings", 1000)) {
		t.Fatal("UI records were replaced by the model budget")
	}
}
