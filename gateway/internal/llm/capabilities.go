package llm

import (
	"encoding/json"
	"os"
)

type ModelCapability struct {
	ImageInput bool   `json:"image_input"`
	VerifiedAt string `json:"verified_at,omitempty"`
	Evidence   string `json:"evidence,omitempty"`
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
