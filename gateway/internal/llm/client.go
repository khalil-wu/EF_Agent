// Package llm is a minimal OpenAI-compatible chat-completions client with streaming tool calls.
// Works with DeepSeek, Qwen (DashScope compatible mode), OpenAI, vLLM and Ollama.
package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"time"
)

type Message struct {
	MessageID        string             `json:"message_id,omitempty"`
	Role             string             `json:"role"` // system | user | assistant | tool
	Content          string             `json:"content,omitempty"`
	ToolCalls        []ToolCall         `json:"tool_calls,omitempty"`
	ToolCallID       string             `json:"tool_call_id,omitempty"`
	Name             string             `json:"name,omitempty"`
	Presentation     *InputPresentation `json:"presentation,omitempty"`
	ToolPresentation json.RawMessage    `json:"tool_presentation,omitempty"` // full UI delivery fields; omitted from provider requests
	Phase            string             `json:"phase,omitempty"`
	CitationIDs      []string           `json:"citation_ids,omitempty"`
	Images           []ImageInput       `json:"-"` // materialized only for provider requests, never saved as base64 in the transcript
}

type ImageInput struct {
	Path      string
	MediaType string
	Name      string
}

type contentPart struct {
	Type     string    `json:"type"`
	Text     string    `json:"text,omitempty"`
	ImageURL *imageURL `json:"image_url,omitempty"`
}

type imageURL struct {
	URL    string `json:"url"`
	Detail string `json:"detail"`
}

type providerMessage struct {
	Role       string     `json:"role"`
	Content    any        `json:"content,omitempty"`
	ToolCalls  []ToolCall `json:"tool_calls,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
	Name       string     `json:"name,omitempty"`
}

type InputAttachment struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Modality string `json:"modality"`
}

type InputReference struct {
	WorkspaceID string `json:"workspace_id,omitempty"`
	ManifestID  string `json:"manifest_id,omitempty"`
	Path        string `json:"path,omitempty"`
	Name        string `json:"name"`
	Kind        string `json:"kind"`
	Excluded    bool   `json:"excluded,omitempty"`
}

// Presentation is saved for the client transcript and omitted from provider requests.
type InputPresentation struct {
	Text        string            `json:"text"`
	Attachments []InputAttachment `json:"attachments,omitempty"`
	References  []InputReference  `json:"references,omitempty"`
	Questions   []string          `json:"questions,omitempty"`
}

type ToolCall struct {
	ID             string `json:"id"`
	Type           string `json:"type"`
	ArgumentsError string `json:"arguments_error,omitempty"`
	Function       struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
	} `json:"function"`
}

// ToolArgumentsError validates provider input before the harness treats it as executable JSON.
func ToolArgumentsError(raw json.RawMessage) string {
	var arguments map[string]json.RawMessage
	if err := json.Unmarshal(raw, &arguments); err != nil {
		return fmt.Sprintf("模型返回的工具参数不是完整的 JSON 对象，本次调用未执行：%v。请重新生成调用参数；长脚本可拆分为较短步骤。", err)
	}
	if arguments == nil {
		return "模型返回了空参数，本次调用未执行。请按工具要求提供 JSON 对象。"
	}
	return ""
}

type ToolSpec struct {
	Type     string `json:"type"`
	Function struct {
		Name        string          `json:"name"`
		Description string          `json:"description"`
		Parameters  json.RawMessage `json:"parameters"`
	} `json:"function"`
}

func NewToolSpec(name, desc string, params json.RawMessage) ToolSpec {
	var t ToolSpec
	t.Type = "function"
	t.Function.Name = name
	t.Function.Description = desc
	if len(params) == 0 {
		params = json.RawMessage(`{"type":"object","properties":{}}`)
	}
	t.Function.Parameters = params
	return t
}

// Event is one streaming delta.
type Event struct {
	Type string // text_delta | reasoning_delta | tool_call_delta | done | error
	Text string
	// tool_call_delta fields
	Index        int
	ID           string
	Name         string
	Args         string
	Err          error
	Usage        *Usage
	FinishReason string
}

type Usage struct {
	PromptTokens        int `json:"prompt_tokens"`
	CompletionTokens    int `json:"completion_tokens"`
	PromptTokensDetails *struct {
		CachedTokens *int `json:"cached_tokens"`
	} `json:"prompt_tokens_details,omitempty"`
	PromptCacheHitTokens *int `json:"prompt_cache_hit_tokens,omitempty"`
}

func (u *Usage) CachedTokens() *int {
	if u.PromptCacheHitTokens != nil {
		return u.PromptCacheHitTokens
	}
	if u.PromptTokensDetails != nil {
		return u.PromptTokensDetails.CachedTokens
	}
	return nil
}

type Client struct {
	BaseURL   string
	APIKey    string
	Model     string
	MaxTokens int
	HTTP      *http.Client
}

const DefaultMaxTokens = 16384

var ErrOutputLimit = errors.New("模型输出达到长度上限")

func New(baseURL, apiKey, model string) *Client {
	// Relays often close idle keep-alive connections silently; reusing one surfaces as `Post ...: EOF`.
	// A short idle timeout keeps the pool fresh, and Stream retries whatever still slips through.
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.IdleConnTimeout = 15 * time.Second
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), APIKey: apiKey, Model: model, MaxTokens: DefaultMaxTokens,
		HTTP: &http.Client{Timeout: 10 * time.Minute, Transport: tr}}
}

const maxAttempts = 4

// retryable reports whether a failed attempt may be repeated: network errors (EOF, reset, timeout
// dialing) and gateway-side statuses. Only called before any delta reached the caller.
func retryable(err error, status int) bool {
	if status == 429 || status == 500 || status == 502 || status == 503 || status == 504 || status == 520 || status == 524 {
		return true
	}
	if err == nil {
		return false
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	var ne net.Error
	return errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) || errors.As(err, &ne) ||
		strings.Contains(err.Error(), "connection reset") || strings.Contains(err.Error(), "forcibly closed")
}

type request struct {
	Model         string            `json:"model"`
	Messages      []providerMessage `json:"messages"`
	Tools         []ToolSpec        `json:"tools,omitempty"`
	Stream        bool              `json:"stream"`
	StreamOptions map[string]any    `json:"stream_options,omitempty"`
	Temperature   float64           `json:"temperature"`
	MaxTokens     int               `json:"max_tokens"`
}

// Stream sends one chat completion request and pushes deltas to out. It always closes out.
// Transient failures are retried with backoff as long as nothing has been emitted yet, so the
// caller never sees a half-streamed answer twice.
func (c *Client) Stream(ctx context.Context, msgs []Message, tools []ToolSpec, out chan<- Event) {
	defer close(out)
	providerMessages := make([]providerMessage, len(msgs))
	for index, message := range msgs {
		var content any = message.Content
		if len(message.Images) > 0 {
			parts := []contentPart{{Type: "text", Text: message.Content}}
			for _, input := range message.Images {
				data, err := os.ReadFile(input.Path)
				if err != nil {
					out <- Event{Type: "error", Err: fmt.Errorf("读取图片 %s: %w", input.Name, err)}
					return
				}
				parts = append(parts, contentPart{Type: "text", Text: "图片：" + input.Name},
					contentPart{Type: "image_url", ImageURL: &imageURL{URL: "data:" + input.MediaType + ";base64," + base64.StdEncoding.EncodeToString(data), Detail: "high"}})
			}
			content = parts
		}
		providerMessages[index] = providerMessage{Role: message.Role, Content: content, ToolCallID: message.ToolCallID, Name: message.Name,
			ToolCalls: append([]ToolCall(nil), message.ToolCalls...)}
		for call := range providerMessages[index].ToolCalls {
			providerMessages[index].ToolCalls[call].ArgumentsError = ""
		}
	}
	body, _ := json.Marshal(request{Model: c.Model, Messages: providerMessages, Tools: tools, Stream: true,
		StreamOptions: map[string]any{"include_usage": true}, Temperature: 0.2, MaxTokens: c.MaxTokens})
	var err error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		if attempt > 1 {
			select {
			case <-ctx.Done():
				out <- Event{Type: "error", Err: ctx.Err()}
				return
			case <-time.After(time.Duration(1<<(attempt-2)) * time.Second):
			}
		}
		var emitted bool
		var status int
		emitted, status, err = c.attempt(ctx, body, out)
		if err == nil || emitted || !retryable(err, status) {
			break
		}
		log.Printf("llm attempt %d/%d failed, retrying: %v", attempt, maxAttempts, err)
	}
	if err != nil {
		out <- Event{Type: "error", Err: err}
	}
}

// attempt runs one HTTP round trip. emitted reports whether any delta was sent to out.
func (c *Client) attempt(ctx context.Context, body []byte, out chan<- Event) (emitted bool, status int, err error) {
	req, err := http.NewRequestWithContext(ctx, "POST", c.BaseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return false, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.APIKey)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return false, 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		b, readErr := io.ReadAll(resp.Body)
		if readErr != nil {
			return false, resp.StatusCode, readErr
		}
		var apiError struct {
			Error struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		message := http.StatusText(resp.StatusCode)
		if resp.StatusCode == 522 {
			message = "Cloudflare could not connect to the origin server"
		}
		if json.Unmarshal(b, &apiError) == nil && apiError.Error.Message != "" {
			message = apiError.Error.Message
		}
		requestID := resp.Header.Get("X-Request-Id")
		if requestID == "" {
			requestID = resp.Header.Get("CF-Ray")
		}
		return false, resp.StatusCode, fmt.Errorf("llm %d: %s (host=%s request_id=%s)", resp.StatusCode, message, req.URL.Host, requestID)
	}
	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	var usage *Usage
	var finishReason string
	finished := false
	for sc.Scan() {
		line := sc.Text()
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if data == "[DONE]" {
			finished = true
			break
		}
		var chunk struct {
			Choices []struct {
				Delta struct {
					Content   string `json:"content"`
					Reasoning string `json:"reasoning_content"`
					ToolCalls []struct {
						Index    int    `json:"index"`
						ID       string `json:"id"`
						Function struct {
							Name      string `json:"name"`
							Arguments string `json:"arguments"`
						} `json:"function"`
					} `json:"tool_calls"`
				} `json:"delta"`
				FinishReason *string `json:"finish_reason"`
			} `json:"choices"`
			Usage *Usage `json:"usage"`
			Error *struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			return emitted, 0, fmt.Errorf("invalid LLM stream: %w", err)
		}
		if chunk.Error != nil {
			return emitted, 0, fmt.Errorf("LLM: %s", chunk.Error.Message)
		}
		if chunk.Usage != nil {
			usage = chunk.Usage
		}
		for _, ch := range chunk.Choices {
			if ch.FinishReason != nil {
				finished = true
				finishReason = *ch.FinishReason
			}
			if ch.Delta.Reasoning != "" {
				out <- Event{Type: "reasoning_delta", Text: ch.Delta.Reasoning}
				emitted = true
			}
			if ch.Delta.Content != "" {
				out <- Event{Type: "text_delta", Text: ch.Delta.Content}
				emitted = true
			}
			for _, tc := range ch.Delta.ToolCalls {
				out <- Event{Type: "tool_call_delta", Index: tc.Index, ID: tc.ID, Name: tc.Function.Name, Args: tc.Function.Arguments}
				emitted = true
			}
		}
	}
	if err := sc.Err(); err != nil {
		return emitted, 0, err
	}
	if !finished {
		return emitted, 0, io.ErrUnexpectedEOF
	}
	out <- Event{Type: "done", Usage: usage, FinishReason: finishReason}
	return true, 0, nil
}
