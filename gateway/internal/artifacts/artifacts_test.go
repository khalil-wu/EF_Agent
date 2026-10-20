package artifacts

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestDownloadFilenameDistinguishesActualProcessingAndVersion(t *testing.T) {
	first := &Record{ArtifactID: "art_123456789abc", Kind: "plot", TimeRangeS: []float64{0, 10}, Summary: json.RawMessage(`{"kind":"clean_asr"}`), Provenance: Provenance{Steps: []string{"asr_k20"}}}
	second := &Record{ArtifactID: "art_abcdef123456", Kind: "plot", TimeRangeS: []float64{0, 10}, Summary: json.RawMessage(`{"kind":"clean_asr"}`), Provenance: Provenance{Steps: []string{"asr_k30"}}}
	one, two := DownloadFilename(t.TempDir(), first, ".svg"), DownloadFilename(t.TempDir(), second, ".svg")
	if one == two || !strings.Contains(one, "ASR20") || !strings.Contains(two, "ASR30") || !strings.Contains(one, "0-10秒") || !strings.HasSuffix(one, "123456789abc.svg") {
		t.Fatal(one, two)
	}
}

func TestLoadAndCompactRoundTripCompleteProvenance(t *testing.T) {
	for _, nullableRun := range []bool{false, true} {
		name := "model_run"
		if nullableRun {
			name = "explicit_null_run"
		}
		t.Run(name, func(t *testing.T) {
			provenance := map[string]any{
				"created_by": "model_prediction@1.0", "created_at": "2026-10-12T12:34:56Z",
				"params": map[string]any{"windows_id": "art_234567", "input_recipe": map[string]any{
					"sample_rate_hz": 128, "channel_order": []string{"F3", "F4"}, "window_s": 1,
				}},
				"steps":  []string{"unit_uV", "bandpass_0.5_45", "model_inference"},
				"job_id": "job_eeg_1", "model_run_id": "run_eeg_1", "model_id": "emotion_eeg_v1",
				"weight_sha256": strings.Repeat("a", 64), "window_policy": "one_second_nonoverlap",
			}
			if nullableRun {
				for _, key := range []string{"job_id", "model_run_id", "model_id", "weight_sha256", "window_policy"} {
					provenance[key] = nil
				}
			}
			raw, err := json.Marshal(map[string]any{
				"artifact_id": "art_abcdef", "kind": "prediction", "uri": "art_abcdef.data.json",
				"source_manifest_ids": []string{"sig_123456"}, "provenance": provenance,
			})
			if err != nil {
				t.Fatal(err)
			}
			dir := t.TempDir()
			if err := os.MkdirAll(filepath.Join(dir, "artifacts"), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(dir, "artifacts", "art_abcdef.json"), raw, 0o644); err != nil {
				t.Fatal(err)
			}
			record, err := Load(dir, "art_abcdef")
			if err != nil {
				t.Fatal(err)
			}
			if !record.HasStep("bandpass") || record.HasStep("asr") {
				t.Fatal("loaded processing steps no longer reflect the source record")
			}
			detail, err := json.Marshal(record)
			if err != nil {
				t.Fatal(err)
			}
			var source struct {
				Provenance map[string]any `json:"provenance"`
			}
			if err := json.Unmarshal(raw, &source); err != nil {
				t.Fatal(err)
			}
			compact := Compact(raw)
			for _, view := range []json.RawMessage{detail, compact, Compact(compact)} {
				var restored struct {
					URI        string         `json:"uri"`
					Provenance map[string]any `json:"provenance"`
				}
				if err := json.Unmarshal(view, &restored); err != nil {
					t.Fatal(err)
				}
				if restored.URI != "art_abcdef.data.json" || !reflect.DeepEqual(restored.Provenance, source.Provenance) {
					t.Fatalf("artifact projection lost provenance or payload identity: %s", view)
				}
			}
		})
	}
}

func TestPredictionDecisionFieldsSurviveLargeResultCompaction(t *testing.T) {
	raw, _ := json.Marshal(map[string]any{
		"artifact_id": "art_abcdef", "kind": "prediction", "uri": "prediction.json",
		"summary": map[string]any{
			"model_id": "classifier", "score_kind": "logits", "prediction_count": 60,
			"label_definitions":       []map[string]string{{"label": "HVLA", "label_text": "高效价、低唤醒"}},
			"decision_margin":         map[string]any{"min": 0.2, "closest_windows": []map[string]any{{"time_range_s": []float64{33, 34}, "label": "HVLA", "runner_up": "HVHA", "margin": 0.2}}},
			"irrelevant_large_detail": strings.Repeat("x", 6000),
		},
	})
	result := string(Compact(raw))
	for _, expected := range []string{"decision_margin", "label_definitions", "高效价、低唤醒", "[33,34]"} {
		if !strings.Contains(result, expected) {
			t.Fatalf("lost %s: %s", expected, result)
		}
	}
}

func TestCompactPreservesScientificMetadataAndPagesLongGeometry(t *testing.T) {
	var segments []map[string]any
	var ranges [][]float64
	for i := 0; i < 100; i++ {
		segments = append(segments, map[string]any{"epoch_index": i, "epoch_tmin_s": -0.2, "event_zero_sample": 50, "packed_start_sample": i * 250})
		ranges = append(ranges, []float64{float64(i), float64(i) + 1})
	}
	raw, _ := json.Marshal(map[string]any{
		"artifact_id": "art_abcdef", "kind": "features", "source_manifest_ids": []string{"sig_123456"}, "parent_artifact_ids": []string{"art_234567"},
		"signal":   map[string]any{"kind": "epochs", "amplitude_unit": "µV", "channel_names": []string{"C3", "C4"}, "segments": segments},
		"analysis": map[string]any{"original_time_ranges_s": ranges, "time_axis": "event-relative", "settings": map[string]any{"welch": map[string]any{"nperseg": 250}}},
		"summary":  map[string]any{"channel_names": []string{"C3", "C4"}, "power_unit": "µV²", "detail": strings.Repeat("d", 5000)},
	})
	var view map[string]json.RawMessage
	if err := json.Unmarshal(Compact(raw), &view); err != nil {
		t.Fatal(err)
	}
	for _, pair := range []struct {
		key      string
		required []string
	}{
		{"source_manifest_ids", []string{"sig_123456"}},
		{"parent_artifact_ids", []string{"art_234567"}},
		{"signal", []string{`"n_segments":100`, `"segments_preview"`, `"epoch_tmin_s":-0.2`, "µV", "C3", `"epoch_index":99`}},
		{"analysis", []string{`"n_time_ranges":100`, `"original_time_ranges_s_preview"`, "event-relative", `"nperseg":250`}},
		{"summary", []string{"C3", "µV²"}},
	} {
		for _, text := range pair.required {
			if !strings.Contains(string(view[pair.key]), text) {
				t.Fatalf("lost %s in %s: %s", text, pair.key, view[pair.key])
			}
		}
	}
	if strings.Contains(string(view["signal"]), `"segments":`) || strings.Contains(string(view["analysis"]), `"original_time_ranges_s":`) {
		t.Fatal("full geometry should stay in the source artifact")
	}
}

func TestCompactRetainsFigureIdentityDownloadsAndAssumptions(t *testing.T) {
	raw, _ := json.Marshal(map[string]any{
		"artifact_id": "art_abcdef", "kind": "plot", "media_type": "image/png", "time_range_s": []int{0, 63},
		"summary": map[string]any{"kind": "psd", "available_formats": []string{"png", "svg", "pdf"},
			"unit_note": "Assumed µV", "sample_rate_note": "Assumed 128 Hz", "channel_peaks_shown": strings.Repeat("large detail", 500)},
	})
	compact := Compact(raw)
	var result struct {
		MediaType string `json:"media_type"`
		TimeRange []int  `json:"time_range_s"`
		Summary   struct {
			Kind    string   `json:"kind"`
			Formats []string `json:"available_formats"`
			Unit    string   `json:"unit_note"`
			Rate    string   `json:"sample_rate_note"`
			Omitted bool     `json:"details_omitted"`
		} `json:"summary"`
	}
	if err := json.Unmarshal(compact, &result); err != nil {
		t.Fatal(err)
	}
	if len(compact) > 2400 || result.MediaType != "image/png" || result.Summary.Kind != "psd" || len(result.Summary.Formats) != 3 || result.Summary.Unit == "" || result.Summary.Rate == "" || !result.Summary.Omitted || result.TimeRange[1] != 63 {
		t.Fatal(string(compact))
	}
}

func TestCompactRetainsEmotionDecisionWhenWindowDetailsAreLarge(t *testing.T) {
	for _, assessmentStatus := range []string{"hypothesis", "unassessed"} {
		t.Run(assessmentStatus, func(t *testing.T) {
			var label any = "HVLA"
			labels := []string{"HVHA", "HVLA", "LVHA", "LVLA"}
			if assessmentStatus == "unassessed" {
				label, labels = nil, []string{}
			}
			var preview []map[string]any
			for i := 0; i < 8; i++ {
				preview = append(preview, map[string]any{
					"window_start": i, "window_end": i, "start_s": float64(i), "end_s": float64(i + 1),
					"assessment_status": assessmentStatus, "label": label,
					"rationale": strings.Repeat("Observed alpha power with residual artifact considerations. ", 40),
				})
			}
			raw, _ := json.Marshal(map[string]any{
				"artifact_id": "art_abcdef", "kind": "emotion_assessment", "uri": "art_abcdef.data.json", "time_range_s": []int{27, 57},
				"summary": map[string]any{"method": "llm_tool_reasoning", "assessment_status": assessmentStatus, "label": label, "labels": labels,
					"rationale": "逐窗特征的测量发现。", "features_id": "art_123456", "windows_id": "art_234567", "source_id": "sig_123456",
					"measured_features": []string{"bandpower", "asymmetry"}, "time_axis": "source_time_s",
					"original_recording_time_range_s": []int{30, 60}, "coverage": map[string]any{"complete": true, "covered_duration_s": 30},
					"prediction_count": 300, "prediction_preview": preview},
			})
			var result struct {
				URI     string         `json:"uri"`
				Summary map[string]any `json:"summary"`
			}
			if err := json.Unmarshal(Compact(raw), &result); err != nil {
				t.Fatal(err)
			}
			for _, key := range []string{"method", "assessment_status", "label", "labels", "rationale", "features_id", "windows_id", "source_id", "measured_features", "time_axis", "coverage", "original_recording_time_range_s", "prediction_count"} {
				if _, ok := result.Summary[key]; !ok {
					t.Fatalf("emotion review lost %s: %v", key, result.Summary)
				}
			}
			rows := result.Summary["prediction_preview"].([]any)
			if len(rows) == 0 || len(rows) >= len(preview) || result.Summary["prediction_preview_count"] != float64(len(rows)) {
				t.Fatalf("large preview must retain a bounded nonempty prefix: %v", result.Summary)
			}
			first := rows[0].(map[string]any)
			if first["window_start"] != float64(0) || first["assessment_status"] != assessmentStatus || first["label"] != label || result.Summary["label"] != label {
				t.Fatalf("preview changed assessment status or label: %v", result.Summary)
			}
			if result.URI == "" || result.Summary["prediction_count"] != float64(300) || result.Summary["prediction_preview_omitted"] != true || !strings.Contains(result.Summary["prediction_lookup_note"].(string), "part=predictions") {
				t.Fatal("omitted assessment rows lack their count or payload/paged-read route")
			}
		})
	}
}

func TestCompactPreservesClassificationAndRegressionContracts(t *testing.T) {
	cases := []struct {
		name        string
		outputKind  string
		scoreKind   string
		calibration map[string]any
	}{
		{"class_probabilities", "classification", "probabilities", map[string]any{"status": "validated", "method": "temperature_scaling", "temperature": 1.2}},
		{"class_logits", "classification", "logits", map[string]any{"status": "uncalibrated"}},
		{"regression", "regression", "", map[string]any{"status": "unknown"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var preview []map[string]any
			for i := 0; i < 12; i++ {
				row := map[string]any{
					"window_index": i, "start_s": float64(i), "end_s": float64(i + 1),
					"epoch_index": i, "epoch_relative_time_range_s": []float64{-0.2, 0.8},
				}
				if tc.outputKind == "classification" {
					row["label"], row["scores"] = "HVLA", []float64{0.2, 0.8}
					if tc.scoreKind == "logits" {
						row["scores"] = []float64{-0.3, 1.9}
					}
				} else {
					row["values"] = []float64{6.2, 4.5}
				}
				preview = append(preview, row)
			}
			summary := map[string]any{
				"method": "model_inference", "model_id": "emotion_eeg_v1", "model_version": "1.2", "run_id": "run_eeg_1",
				"model_evaluation": map[string]any{"status": "test_selected", "independent_test": false},
				"task":             "emotion_recognition", "output_kind": tc.outputKind, "calibration": tc.calibration,
				"source_id": "sig_123456", "windows_id": "art_234567", "time_axis": "epoch-relative",
				"coverage":    map[string]any{"complete": true, "covered_duration_s": 12},
				"aggregation": nil, "prediction_count": 12, "prediction_preview": preview,
				"optional_description": strings.Repeat("detail ", 900),
			}
			if tc.outputKind == "classification" {
				summary["labels"], summary["score_kind"] = []string{"HVHA", "HVLA"}, tc.scoreKind
				summary["label_counts"] = map[string]int{"HVHA": 2, "HVLA": 10}
			} else {
				summary["dimensions"] = []map[string]any{{"name": "valence", "unit": "SAM_1_9"}, {"name": "arousal", "unit": "SAM_1_9"}}
				summary["aggregation"] = map[string]any{"method": "trial_mean", "n_windows": 12, "values": []float64{6.2, 4.5}}
			}
			raw, _ := json.Marshal(map[string]any{
				"artifact_id": "art_abcdef", "kind": "prediction", "uri": "art_abcdef.data.json",
				"media_type": "application/json", "source_manifest_ids": []string{"sig_123456"}, "summary": summary,
				"provenance": map[string]any{"created_by": "model_prediction@1.0", "created_at": "2026-10-12T12:34:56Z", "params": map[string]any{}, "steps": []string{"model_inference"}, "model_run_id": "run_eeg_1", "model_id": "emotion_eeg_v1", "weight_sha256": nil},
			})
			var source struct {
				Summary map[string]json.RawMessage `json:"summary"`
			}
			if err := json.Unmarshal(raw, &source); err != nil {
				t.Fatal(err)
			}
			compact := Compact(raw)
			for _, projected := range []json.RawMessage{compact, Compact(compact)} {
				var view struct {
					ArtifactID string                     `json:"artifact_id"`
					URI        string                     `json:"uri"`
					Summary    map[string]json.RawMessage `json:"summary"`
				}
				if err := json.Unmarshal(projected, &view); err != nil {
					t.Fatal(err)
				}
				for _, key := range []string{"method", "model_id", "model_version", "model_evaluation", "run_id", "task", "output_kind", "score_kind", "calibration", "labels", "label_counts", "dimensions", "aggregation", "prediction_count", "source_id", "windows_id", "time_axis", "coverage"} {
					if string(view.Summary[key]) != string(source.Summary[key]) {
						t.Fatalf("changed model output contract field %s: %s", key, projected)
					}
				}
				if _, kept := view.Summary["optional_description"]; kept {
					t.Fatal("descriptive bulk must not displace model output metadata")
				}
				var rows, originalRows []json.RawMessage
				if err := json.Unmarshal(view.Summary["prediction_preview"], &rows); err != nil {
					t.Fatal(err)
				}
				if err := json.Unmarshal(source.Summary["prediction_preview"], &originalRows); err != nil {
					t.Fatal(err)
				}
				if len(rows) < 1 || len(rows) > 8 {
					t.Fatalf("unbounded or empty prediction preview: %d", len(rows))
				}
				for i, row := range rows {
					if string(row) != string(originalRows[i]) {
						t.Fatalf("preview changed numerical scores/values or epoch identity: %s", row)
					}
				}
				if string(view.Summary["prediction_preview_count"]) != strconv.Itoa(len(rows)) || string(view.Summary["prediction_preview_omitted"]) != "true" || !strings.Contains(string(view.Summary["prediction_lookup_note"]), "part=predictions") || view.ArtifactID != "art_abcdef" || view.URI == "" {
					t.Fatalf("preview is not traceable to complete paged rows: %s", projected)
				}
			}
		})
	}
}

func TestCompactBoundsLongEpochCoverageWithoutLosingCoordinateIdentity(t *testing.T) {
	var coordinates []map[string]any
	var intervals [][]float64
	for i := 0; i < 100; i++ {
		coordinates = append(coordinates, map[string]any{
			"epoch_index": i, "original_time_range_s": []float64{50 + float64(i)*10, 51 + float64(i)*10},
			"epoch_relative_time_range_s": []float64{-0.2, 0.8},
		})
		intervals = append(intervals, []float64{float64(i) * 2, float64(i)*2 + 1})
	}
	raw, _ := json.Marshal(map[string]any{
		"artifact_id": "art_abcdef", "kind": "emotion_assessment", "uri": "art_abcdef.data.json",
		"source_manifest_ids": []string{"sig_123456"},
		"analysis":            map[string]any{"time_axis": "epoch-relative", "original_time_ranges_s": coordinates},
		"summary": map[string]any{
			"method": "llm_tool_reasoning", "assessment_status": "unassessed", "label": nil,
			"time_axis": "epoch-relative", "prediction_count": 1,
			"coverage":           map[string]any{"complete": false, "covered_duration_s": 100, "n_windows": 100, "intervals_s": intervals, "original_time_ranges_s": coordinates},
			"aggregation":        map[string]any{"method": "measurement_review", "original_time_ranges_s": coordinates},
			"prediction_preview": []map[string]any{{"window_start": 0, "window_end": 99, "assessment_status": "unassessed", "label": nil, "observed_intervals_s": intervals, "original_time_ranges_s": coordinates}},
		},
	})
	var result map[string]json.RawMessage
	if err := json.Unmarshal(Compact(raw), &result); err != nil {
		t.Fatal(err)
	}
	var summary map[string]json.RawMessage
	if err := json.Unmarshal(result["summary"], &summary); err != nil {
		t.Fatal(err)
	}
	var rows []json.RawMessage
	if err := json.Unmarshal(summary["prediction_preview"], &rows); err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || string(summary["prediction_preview_count"]) != "1" {
		t.Fatal("the single review must remain visible despite its long coordinate lists")
	}
	for name, rawView := range map[string]json.RawMessage{"coverage": summary["coverage"], "aggregation": summary["aggregation"], "analysis": result["analysis"], "prediction_row": rows[0]} {
		var view map[string]json.RawMessage
		if err := json.Unmarshal(rawView, &view); err != nil {
			t.Fatal(err)
		}
		if string(view["n_time_ranges"]) != "100" || string(view["original_time_ranges_s_omitted"]) != "true" || len(view["original_time_ranges_s"]) > 0 || len(view["coordinate_lookup_note"]) == 0 {
			t.Fatalf("%s lost coordinate extent or kept the full list: %s", name, rawView)
		}
		var preview []struct {
			EpochIndex int       `json:"epoch_index"`
			Original   []float64 `json:"original_time_range_s"`
			Relative   []float64 `json:"epoch_relative_time_range_s"`
		}
		if err := json.Unmarshal(view["original_time_ranges_s_preview"], &preview); err != nil {
			t.Fatal(err)
		}
		if len(preview) != 4 {
			t.Fatalf("%s should show bounded first/last coordinates, got %d", name, len(preview))
		}
		for i, epoch := range []int{0, 1, 98, 99} {
			row := preview[i]
			if row.EpochIndex != epoch || !reflect.DeepEqual(row.Original, []float64{50 + float64(epoch)*10, 51 + float64(epoch)*10}) || !reflect.DeepEqual(row.Relative, []float64{-0.2, 0.8}) {
				t.Fatalf("%s changed epoch/source coordinates: %+v", name, row)
			}
		}
		if name == "coverage" && (string(view["n_intervals"]) != "100" || string(view["complete"]) != "false" || string(view["covered_duration_s"]) != "100") {
			t.Fatal("bounded coverage changed completeness or measured duration")
		}
		if name == "prediction_row" && (string(view["n_observed_intervals"]) != "100" || string(view["assessment_status"]) != `"unassessed"` || string(view["label"]) != "null") {
			t.Fatal("coordinate projection changed the review status or its null label")
		}
	}
}
