package llm

import (
	"encoding/json"
	"os"
	"strings"
)

type ModelCapability struct {
	ImageInput bool   `json:"image_input"`
	VerifiedAt string `json:"verified_at,omitempty"`
	Evidence   string `json:"evidence,omitempty"`
	Source     string `json:"source,omitempty"`
}

var inferredVisionPrefixes = []string{
	"minimax-m3",
	"minimax-vl",
	"gpt-4o",
	"gpt-4.1",
	"chatgpt-4o",
	"claude-3-",
	"claude-sonnet-4",
	"claude-opus-4",
	"claude-haiku-4",
	"gemini-1.5",
	"gemini-2.",
	"gemini-3",
	"qwen-vl",
	"qwen2-vl",
	"qwen2.5-vl",
	"qwen3-vl",
	"qvq",
	"glm-4v",
	"glm-4.1v",
	"glm-4.5v",
	"pixtral",
	"internvl",
	"llava",
	"yi-vision",
	"doubao-1-5-vision",
	"doubao-seed-1-6",
	"kimi-vl",
}

// InferImageCapability recognizes well-known multimodal vision model families when not explicitly configured.
func InferImageCapability(model string) bool {
	lower := strings.ToLower(strings.TrimSpace(model))
	if lower == "" {
		return false
	}
	// Strip provider prefix like "openai/gpt-4o" or "qwen/qwen2.5-vl-72b" if present.
	if idx := strings.LastIndex(lower, "/"); idx >= 0 && idx+1 < len(lower) {
		lower = lower[idx+1:]
	}
	for _, token := range inferredVisionPrefixes {
		if strings.Contains(lower, token) {
			return true
		}
	}
	return false
}

// CapabilityFor resolves model capability by exact lookup, case-insensitive lookup, then family inference.
func CapabilityFor(model string, configured map[string]ModelCapability) ModelCapability {
	trimmed := strings.TrimSpace(model)
	if trimmed == "" {
		return ModelCapability{}
	}
	if cap, ok := configured[trimmed]; ok {
		if cap.ImageInput && cap.Source == "" {
			cap.Source = "verified"
		}
		return cap
	}
	for key, cap := range configured {
		if strings.EqualFold(strings.TrimSpace(key), trimmed) {
			if cap.ImageInput && cap.Source == "" {
				cap.Source = "verified"
			}
			return cap
		}
	}
	if InferImageCapability(trimmed) {
		return ModelCapability{ImageInput: true, Source: "inferred"}
	}
	return ModelCapability{}
}

func LoadCapabilities(path string) (map[string]ModelCapability, error) {
	body, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var config struct {
		Models map[string]ModelCapability `json:"models"`
	}
	if err := json.Unmarshal(body, &config); err != nil {
		return nil, err
	}
	return config.Models, nil
}
