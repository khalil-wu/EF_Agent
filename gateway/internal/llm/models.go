package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// WithModel reuses the provider connection without changing other conversations.
func (c *Client) WithModel(model string) *Client {
	if model == "" {
		return c
	}
	selected := *c
	selected.Model = model
	return &selected
}

func (c *Client) Models(ctx context.Context) ([]string, error) {
	request, err := http.NewRequestWithContext(ctx, "GET", c.BaseURL+"/models", nil)
	if err != nil {
		return nil, err
	}
	if c.APIKey != "" {
		request.Header.Set("Authorization", "Bearer "+c.APIKey)
	}
	response, err := c.HTTP.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("网关模型列表返回 HTTP %d", response.StatusCode)
	}
	var result struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		return nil, err
	}
	models := make([]string, len(result.Data))
	for index, row := range result.Data {
		models[index] = row.ID
	}
	return models, nil
}
