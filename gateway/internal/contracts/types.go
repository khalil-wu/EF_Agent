// Package contracts holds the Go types for the JSON contracts in /contracts.
// They mirror manifest.schema.json, artifact.schema.json, job.schema.json and tool.schema.json.
// Keep field names identical to the schema; the Python worker produces the same shapes.
package contracts

import "encoding/json"

// Inferred is a value plus its evidence source. File observations, user declarations and
// format assumptions stay distinct; the legacy dataset_adapter source is a format assumption.
type Inferred struct {
	Value      json.RawMessage `json:"value"`
	Source     string          `json:"source"`
	Confidence *float64        `json:"confidence,omitempty"`
}

// CompatResult is one row of check_model_compat.
type CompatResult struct {
	ModelID         string   `json:"model_id"`
	Status          string   `json:"status"` // compatible | mappable | incompatible
	Reasons         []string `json:"reasons"`
	MissingChannels []string `json:"missing_channels,omitempty"`
}

// Manifest is the union of SignalManifest and VideoManifest; unused fields stay zero.
type Manifest struct {
	ManifestID       string            `json:"manifest_id"`
	Modality         string            `json:"modality"`
	AttachmentSHA256 string            `json:"attachment_sha256"`
	OriginalName     string            `json:"original_name,omitempty"`
	UploadGroupID    string            `json:"upload_group_id,omitempty"`
	SourceFiles      []string          `json:"source_files,omitempty"`
	SourcePath       string            `json:"source_path,omitempty"`
	SourceFormat     string            `json:"source_format,omitempty"`
	Dataset          string            `json:"dataset,omitempty"`
	FormatProfile    string            `json:"format_profile,omitempty"`
	MetadataEvidence json.RawMessage   `json:"metadata_evidence,omitempty"`
	SubjectID        *string           `json:"subject_id,omitempty"`
	TrialID          *string           `json:"trial_id,omitempty"`
	SampleRateHz     *float64          `json:"sample_rate_hz,omitempty"`
	NChannels        int               `json:"n_channels,omitempty"`
	NSamples         int               `json:"n_samples,omitempty"`
	DurationS        *float64          `json:"duration_s,omitempty"`
	ChannelNames     []string          `json:"channel_names,omitempty"`
	ChannelTypes     []string          `json:"channel_types,omitempty"`
	SignalKind       string            `json:"signal_kind,omitempty"`
	Segments         []json.RawMessage `json:"segments,omitempty"`
	NSegments        int               `json:"n_segments,omitempty"`
	SegmentsPreview  []json.RawMessage `json:"segments_preview,omitempty"`
	EpochTminRangeS  []float64         `json:"epoch_tmin_range_s,omitempty"`
	TimeAxis         string            `json:"time_axis,omitempty"`
	TrialIndex       *int              `json:"trial_index,omitempty"`
	TrialCount       int               `json:"trial_count,omitempty"`
	TrialSelection   string            `json:"trial_selection,omitempty"`
	ChannelLayout    *Inferred         `json:"channel_layout,omitempty"`
	Unit             *Inferred         `json:"unit,omitempty"`
	Reference        string            `json:"reference,omitempty"`
	Events           []json.RawMessage `json:"events,omitempty"`
	Labels           json.RawMessage   `json:"labels,omitempty"`
	LabelsAvailable  bool              `json:"labels_available,omitempty"`
	Quality          json.RawMessage   `json:"quality,omitempty"`
	Sync             json.RawMessage   `json:"sync,omitempty"`
	Compat           []CompatResult    `json:"compat"`
	Warnings         []string          `json:"warnings,omitempty"`
	// video-only
	Role           *Inferred `json:"role,omitempty"`
	FPS            *float64  `json:"fps,omitempty"`
	FaceValidRatio *float64  `json:"face_valid_ratio,omitempty"`
	Width          *int      `json:"width,omitempty"`
	Height         *int      `json:"height,omitempty"`
	Audio          *bool     `json:"audio,omitempty"`
	// document and shared attachment metadata
	PageCount     *int            `json:"page_count,omitempty"`
	SizeBytes     *int64          `json:"size_bytes,omitempty"`
	DocumentIndex json.RawMessage `json:"document_index,omitempty"`
	// image input; vision_path is the decoded orientation-correct raster used by the provider.
	MediaType  string `json:"media_type,omitempty"`
	VisionPath string `json:"vision_path,omitempty"`
	FrameCount int    `json:"frame_count,omitempty"`
}

// SameRecord distinguishes selected trials while sharing the original file identity.
func (m *Manifest) SameRecord(other *Manifest) bool {
	if m.AttachmentSHA256 != other.AttachmentSHA256 {
		return false
	}
	if m.TrialIndex == nil || other.TrialIndex == nil {
		return m.TrialIndex == nil && other.TrialIndex == nil
	}
	return *m.TrialIndex == *other.TrialIndex
}

// ToolRequires are the deterministic preconditions checked in before_tool.
type ToolRequires struct {
	ArtifactKind        map[string]string   `json:"artifact_kind,omitempty"`
	ProvenanceSteps     map[string][]string `json:"provenance_steps,omitempty"`
	ManifestFieldsKnown []string            `json:"manifest_fields_known,omitempty"`
	ModelCompat         string              `json:"model_compat,omitempty"`
	SyncRequired        bool                `json:"sync_required,omitempty"`
}

// ToolDefinition mirrors tool.schema.json. Served by the worker at GET /tools.
type ToolDefinition struct {
	Name              string          `json:"name"`
	Version           string          `json:"version"`
	Description       string          `json:"description"`
	InputSchema       json.RawMessage `json:"input_schema"`
	OutputSchema      json.RawMessage `json:"output_schema"`
	Runtime           string          `json:"runtime"`
	Async             bool            `json:"async"`
	ExecutionMode     string          `json:"execution_mode,omitempty"`
	PermissionModeMin string          `json:"permission_mode_min"`
	EstimatedCost     string          `json:"estimated_cost,omitempty"`
	Requires          *ToolRequires   `json:"requires,omitempty"`
}

// PermissionMode is the composer mode; ordered readonly < write_normalized < gpu.
type PermissionMode string

const (
	ModeReadonly        PermissionMode = "readonly"
	ModeWriteNormalized PermissionMode = "write_normalized"
	ModeGPU             PermissionMode = "gpu"
)

func (m PermissionMode) Rank() int {
	switch m {
	case ModeWriteNormalized:
		return 1
	case ModeGPU:
		return 2
	default:
		return 0
	}
}
