// Package artifacts reads artifact records written by the worker (contracts/artifact.schema.json).
// The gateway never opens payloads; it only needs records for requires checks and prompt context.
package artifacts

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

var idRe = regexp.MustCompile(`^art_[0-9a-f]{6,}$`)
var filenameCharsRe = regexp.MustCompile(`[<>:"/\\|?*\x00-\x1f]`)
var filenameSpaceRe = regexp.MustCompile(`\s+`)

func DownloadFilename(storageDir string, rec *Record, extension string) string {
	var summary struct {
		Title string `json:"title"`
		Kind  string `json:"kind"`
	}
	_ = json.Unmarshal(rec.Summary, &summary)
	title := summary.Title
	if title == "" {
		title = map[string]string{"raw": "EEG波形", "psd": "功率谱密度（PSD）", "overview": "EEG分析总览",
			"bandpower_timeline": "频带功率时间线", "clean_ica_iclabel": "ICA成分头皮分布", "clean_asr": "ASR清洗前后对照"}[summary.Kind]
	}
	if title == "" {
		title = map[string]string{"plot": "分析图", "table": "分析数据表", "report": "分析报告", "normalized_signal": "处理后EEG",
			"features": "EEG特征", "windows": "分析窗口", "prediction": "模型预测结果", "emotion_assessment": "情绪审阅记录"}[rec.Kind]
	}
	if title == "" {
		title = "分析产物"
	}
	if strings.Contains("|.png|.svg|.pdf|.csv|.tsv|.json|.md|.html|.py|.npy|.npz|", "|"+strings.ToLower(filepath.Ext(title))+"|") {
		title = strings.TrimSuffix(title, filepath.Ext(title))
	}
	parts := []string{title}
	if len(rec.SourceManifestIDs) > 0 {
		var source struct {
			OriginalName string `json:"original_name"`
			SourcePath   string `json:"source_path"`
			TrialIndex   *int   `json:"trial_index"`
		}
		body, err := os.ReadFile(filepath.Join(storageDir, "manifests", rec.SourceManifestIDs[0]+".json"))
		if err == nil && json.Unmarshal(body, &source) == nil {
			name := source.OriginalName
			if name == "" {
				name = filepath.Base(source.SourcePath)
			}
			parts = append(parts, strings.TrimSuffix(name, filepath.Ext(name)))
			if source.TrialIndex != nil {
				parts = append(parts, "试次"+strconv.Itoa(*source.TrialIndex+1))
			}
		}
	}
	for _, step := range rec.Provenance.Steps {
		if strings.HasPrefix(step, "asr_k") {
			parts = append(parts, "ASR"+strings.TrimPrefix(step, "asr_k"))
		} else if strings.HasPrefix(step, "ica_iclabel_removed_") {
			parts = append(parts, "ICA剔除"+strings.TrimPrefix(step, "ica_iclabel_removed_"))
		}
	}
	if len(rec.TimeRangeS) == 2 {
		parts = append(parts, strconv.FormatFloat(rec.TimeRangeS[0], 'g', -1, 64)+"-"+strconv.FormatFloat(rec.TimeRangeS[1], 'g', -1, 64)+"秒")
	}
	parts = append(parts, "版本"+strings.TrimPrefix(rec.ArtifactID, "art_"))
	stem := filenameCharsRe.ReplaceAllString(strings.Join(parts, "_"), "-")
	return strings.Trim(filenameSpaceRe.ReplaceAllString(stem, ""), ". ") + extension
}

type Provenance struct {
	CreatedBy    string          `json:"created_by"`
	CreatedAt    string          `json:"created_at"`
	Params       json.RawMessage `json:"params"`
	Steps        []string        `json:"steps"`
	JobID        json.RawMessage `json:"job_id,omitempty"`
	ModelRunID   json.RawMessage `json:"model_run_id,omitempty"`
	ModelID      json.RawMessage `json:"model_id,omitempty"`
	WeightSHA    json.RawMessage `json:"weight_sha256,omitempty"`
	WindowPolicy json.RawMessage `json:"window_policy,omitempty"`
}

type Record struct {
	ArtifactID        string                    `json:"artifact_id"`
	Kind              string                    `json:"kind"`
	URI               string                    `json:"uri"`
	MediaType         string                    `json:"media_type,omitempty"`
	Representations   map[string]Representation `json:"representations,omitempty"`
	SourceManifestIDs []string                  `json:"source_manifest_ids"`
	ParentArtifactIDs []string                  `json:"parent_artifact_ids,omitempty"`
	TimeRangeS        []float64                 `json:"time_range_s,omitempty"`
	Shape             []int                     `json:"shape,omitempty"`
	Signal            json.RawMessage           `json:"signal,omitempty"`
	Analysis          json.RawMessage           `json:"analysis,omitempty"`
	Summary           json.RawMessage           `json:"summary,omitempty"`
	Provenance        Provenance                `json:"provenance"`
}

type Representation struct {
	URI       string `json:"uri"`
	MediaType string `json:"media_type"`
}

// Load reads storage/artifacts/<id>.json. The id is validated so a model-supplied string cannot escape the dir.
func Load(storageDir, id string) (*Record, error) {
	if !idRe.MatchString(id) {
		return nil, errors.New("invalid artifact id: " + id)
	}
	b, err := os.ReadFile(filepath.Join(storageDir, "artifacts", id+".json"))
	if err != nil {
		return nil, err
	}
	var r Record
	if err := json.Unmarshal(b, &r); err != nil {
		return nil, err
	}
	return &r, nil
}

// HasStep reports whether any provenance step starts with prefix (e.g. "bandpass" matches "bandpass_0.5_50").
func (r *Record) HasStep(prefix string) bool {
	for _, s := range r.Provenance.Steps {
		if len(s) >= len(prefix) && s[:len(prefix)] == prefix {
			return true
		}
	}
	return false
}

// Compact retains provenance, scientific coordinates and decision fields with bounded previews.
func Compact(raw json.RawMessage) json.RawMessage {
	var r Record
	if err := json.Unmarshal(raw, &r); err != nil {
		return raw
	}
	coordinateLists := [][3]string{
		{"original_time_ranges_s", "n_time_ranges", "original_time_ranges_s_preview"},
		{"intervals_s", "n_intervals", "intervals_s_preview"},
		{"original_intervals_s", "n_original_intervals", "original_intervals_s_preview"},
		{"observed_intervals_s", "n_observed_intervals", "observed_intervals_s_preview"},
	}
	var summary map[string]json.RawMessage
	_ = json.Unmarshal(compactCoordinates(r.Summary, [][3]string{
		{"original_time_ranges_s", "n_time_ranges", "original_time_ranges_s_preview"},
		{"unrepresented_time_ranges_s", "n_unrepresented_time_ranges", "unrepresented_time_ranges_s_preview"},
	}), &summary)
	for _, key := range []string{"coverage", "aggregation"} {
		if value, exists := summary[key]; exists {
			summary[key] = compactCoordinates(value, coordinateLists)
		}
	}
	var preview []json.RawMessage
	_, hasPreview := summary["prediction_preview"]
	if hasPreview {
		_ = json.Unmarshal(summary["prediction_preview"], &preview)
		if len(preview) > 8 {
			preview = preview[:8]
		}
		for i, row := range preview {
			preview[i] = compactCoordinates(row, coordinateLists)
		}
		summary["prediction_preview"], _ = json.Marshal(preview)
	}
	out := map[string]any{
		"artifact_id": r.ArtifactID, "kind": r.Kind, "uri": r.URI, "media_type": r.MediaType, "time_range_s": r.TimeRangeS,
		"created_by": r.Provenance.CreatedBy, "steps": r.Provenance.Steps, "summary": summary,
		"source_manifest_ids": r.SourceManifestIDs, "parent_artifact_ids": r.ParentArtifactIDs,
		"shape": r.Shape, "provenance": r.Provenance,
	}
	if len(r.Representations) > 0 {
		out["representations"] = r.Representations
	}
	if len(r.Signal) > 0 {
		out["signal"] = compactCoordinates(r.Signal, [][3]string{{"segments", "n_segments", "segments_preview"}})
	}
	if len(r.Analysis) > 0 {
		out["analysis"] = compactCoordinates(r.Analysis, [][3]string{
			{"original_time_ranges_s", "n_time_ranges", "original_time_ranges_s_preview"},
			{"window_support", "n_window_support", "window_support_preview"},
		})
	}
	b, _ := json.Marshal(out)
	if len(b) > 2400 {
		brief := map[string]json.RawMessage{"details_omitted": json.RawMessage("true")}
		for _, key := range []string{"kind", "title", "format", "available_formats", "channel_names", "n_channels", "n_windows", "window_s", "stride_s", "peak_hz", "amplitude_unit", "amplitude_unit_source", "power_unit", "unit_note", "sample_rate_note", "time_axis", "original_recording_offset_s", "original_recording_time_range_s", "window_evidence_time_range_s", "original_time_ranges_s", "n_time_ranges", "original_time_ranges_s_preview", "original_time_ranges_s_omitted", "unrepresented_time_ranges_s", "n_unrepresented_time_ranges", "unrepresented_time_ranges_s_preview", "unrepresented_time_ranges_s_omitted", "coordinate_lookup_note", "figure_profile", "figure_width_mm", "panels", "de_method", "de_log_base", "de_unit", "de_input_amplitude_unit", "de_filter", "de_variance_ddof", "feature_components", "feature_units", "feature_component_units", "processing_settings", "connectivity_method", "hjorth_derivative_basis", "rms_centering", "welch", "bands", "method", "assessment_status", "status", "label", "labels", "label_definitions", "dimensions", "rationale", "coverage", "features_id", "windows_id", "source_id", "measured_features", "model_id", "model_version", "model_evaluation", "run_id", "task", "output_kind", "score_kind", "calibration", "aggregation", "prediction_count", "label_counts", "decision_margin", "prediction_preview", "prediction_preview_count", "prediction_preview_omitted", "prediction_lookup_note"} {
			if value, ok := summary[key]; ok {
				brief[key] = value
			}
		}
		summary = brief
		out["summary"] = brief
		b, _ = json.Marshal(out)
	}
	if hasPreview {
		var predictionCount int
		_ = json.Unmarshal(summary["prediction_count"], &predictionCount)
		for {
			summary["prediction_preview"], _ = json.Marshal(preview)
			summary["prediction_preview_count"], _ = json.Marshal(len(preview))
			if predictionCount > len(preview) {
				summary["prediction_preview_omitted"] = json.RawMessage("true")
				summary["prediction_lookup_note"], _ = json.Marshal("完整逐窗结果保存在源产物；使用 read_artifact(artifact_id, part=predictions, start, limit) 分页，或打开产物文件。")
			}
			b, _ = json.Marshal(out)
			if len(b) <= 2400 || len(preview) <= 1 {
				break
			}
			preview = preview[:len(preview)-1]
		}
	}
	return b
}

// Long coordinate lists stay in the full artifact; context carries their extent and lookup route.
func compactCoordinates(raw json.RawMessage, lists [][3]string) json.RawMessage {
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(raw, &fields)
	for _, spec := range lists {
		var rows []json.RawMessage
		_ = json.Unmarshal(fields[spec[0]], &rows)
		if len(rows) <= 4 {
			continue
		}
		fields[spec[1]], _ = json.Marshal(len(rows))
		fields[spec[0]+"_omitted"] = json.RawMessage("true")
		fields[spec[2]], _ = json.Marshal(append(rows[:2:2], rows[len(rows)-2:]...))
		delete(fields, spec[0])
		fields["coordinate_lookup_note"], _ = json.Marshal("完整坐标保存在源产物；segments 用 read_artifact(part=segments) 分页，窗口坐标随数值行返回。")
	}
	out, _ := json.Marshal(fields)
	return out
}
