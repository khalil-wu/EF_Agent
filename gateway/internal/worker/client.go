// Package worker is the HTTP client for the Python signal worker.
package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"eegagent/gateway/internal/contracts"
)

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func New(baseURL string) *Client {
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), HTTP: &http.Client{Timeout: 16 * time.Minute}}
}

func (c *Client) Tools(ctx context.Context) ([]contracts.ToolDefinition, error) {
	req, _ := http.NewRequestWithContext(ctx, "GET", c.BaseURL+"/tools", nil)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("worker /tools: HTTP %d", resp.StatusCode)
	}
	var out []contracts.ToolDefinition
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode /tools: %w", err)
	}
	return out, nil
}

// Call runs a tool. Non-2xx responses are returned as (body, error) so the loop can hand the
// worker's error object back to the model.
func (c *Client) Call(ctx context.Context, name string, args json.RawMessage) (json.RawMessage, error) {
	return c.call(ctx, name, args, nil)
}

func (c *Client) CallWithProgress(ctx context.Context, name string, args json.RawMessage, progress func(json.RawMessage)) (json.RawMessage, error) {
	return c.call(ctx, name, args, progress)
}

func (c *Client) call(ctx context.Context, name string, args json.RawMessage, progress func(json.RawMessage)) (json.RawMessage, error) {
	payload, _ := json.Marshal(map[string]json.RawMessage{"args": args})
	endpoint := c.BaseURL + "/tools/" + name
	if progress != nil {
		endpoint += "/events"
	}
	req, _ := http.NewRequestWithContext(ctx, "POST", endpoint, bytes.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return c.stopCancelled(ctx, name, args), err
	}
	defer resp.Body.Close()
	var body json.RawMessage
	if progress != nil && resp.StatusCode < 300 {
		decoder := json.NewDecoder(resp.Body)
		for {
			var event struct {
				Type     string          `json:"type"`
				Progress json.RawMessage `json:"progress"`
				Result   json.RawMessage `json:"result"`
			}
			if err = decoder.Decode(&event); err != nil {
				break
			}
			if event.Type == "progress" {
				progress(event.Progress)
			}
			if event.Type == "result" {
				body = event.Result
				break
			}
		}
	} else {
		body, err = io.ReadAll(resp.Body)
	}
	if err != nil {
		return c.stopCancelled(ctx, name, args), err
	}
	var outcome struct {
		Status string `json:"status"`
		Error  struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if resp.StatusCode < 300 {
		if err := json.Unmarshal(body, &outcome); err != nil {
			return body, fmt.Errorf("decode worker %s: %w", name, err)
		}
		if outcome.Status == "error" {
			return body, fmt.Errorf("worker %s (%s): %s", name, outcome.Error.Code, outcome.Error.Message)
		}
	}
	if resp.StatusCode >= 300 {
		return body, fmt.Errorf("worker %d", resp.StatusCode)
	}
	return body, nil
}

// Get proxies a GET path (e.g. /knowledge/k_1234abcd) and returns the body.
func (c *Client) Get(ctx context.Context, path string) (json.RawMessage, int, error) {
	req, _ := http.NewRequestWithContext(ctx, "GET", c.BaseURL+path, nil)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, err
	}
	return body, resp.StatusCode, nil
}

func (c *Client) stopCancelled(ctx context.Context, name string, args json.RawMessage) json.RawMessage {
	if (name == "run_python" || name == "run_model" || name == "recognize_emotion" || name == "workspace_quality" || name == "analyze_signal") && ctx.Err() != nil {
		var input struct {
			SessionID string `json:"_session_id"`
		}
		json.Unmarshal(args, &input)
		stopCtx, cancel := context.WithTimeout(context.Background(), 7*time.Second)
		defer cancel()
		stopReq, _ := http.NewRequestWithContext(stopCtx, "POST", c.BaseURL+"/sessions/"+input.SessionID+"/stop", nil)
		response, stopErr := c.HTTP.Do(stopReq)
		if stopErr != nil {
			log.Printf("stop worker execution %s: %v", input.SessionID, stopErr)
		} else {
			defer response.Body.Close()
			if name == "run_python" {
				var stopped struct {
					Result json.RawMessage `json:"result"`
				}
				if err := json.NewDecoder(response.Body).Decode(&stopped); err != nil {
					log.Printf("read stopped script result %s: %v", input.SessionID, err)
				} else {
					return stopped.Result
				}
			}
		}
	}
	return nil
}
