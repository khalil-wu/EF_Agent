package agent

import (
	"encoding/json"
	"fmt"
	"strings"
)

type InputOption struct {
	Label       string `json:"label"`
	Description string `json:"description,omitempty"`
}

type InputQuestion struct {
	ID         string        `json:"id"`
	Question   string        `json:"question"`
	AnswerType string        `json:"answer_type,omitempty"`
	Options    []InputOption `json:"options,omitempty"`
}

type InputRequest struct {
	ID        string          `json:"id"`
	Questions []InputQuestion `json:"questions"`
}

const inputRequestSchema = `{"type":"object","properties":{"questions":{"type":"array","minItems":1,"maxItems":3,"items":{"type":"object","properties":{"id":{"type":"string"},"question":{"type":"string"},"answer_type":{"type":"string","enum":["text","number","choice"],"description":"必须填写。实测数值用 number，自由信息用 text，分类/偏好用 choice。choice 必须提供 options。"},"options":{"type":"array","minItems":2,"maxItems":3,"items":{"type":"object","properties":{"label":{"type":"string"},"description":{"type":"string"}},"required":["label"]}}},"required":["id","question","answer_type"]}}},"required":["questions"]}`

func newInputRequest(id string, args json.RawMessage) (*InputRequest, error) {
	request := &InputRequest{ID: id}
	if err := json.Unmarshal(args, request); err != nil {
		return nil, err
	}
	request.ID = id
	if len(request.Questions) < 1 || len(request.Questions) > 3 {
		return nil, fmt.Errorf("每次提问需要 1–3 个问题")
	}
	ids := map[string]bool{}
	for index := range request.Questions {
		question := &request.Questions[index]
		if question.AnswerType != "text" && question.AnswerType != "number" && question.AnswerType != "choice" {
			return nil, fmt.Errorf("answer_type 需要是 text、number 或 choice")
		}
		if question.AnswerType != "choice" {
			question.Options = nil
		}
		if question.AnswerType == "choice" && len(question.Options) < 2 {
			return nil, fmt.Errorf("分类问题需要 2–3 个选项")
		}
		if question.ID == "" || strings.TrimSpace(question.Question) == "" || ids[question.ID] {
			return nil, fmt.Errorf("问题需要唯一 id 和问题正文")
		}
		ids[question.ID] = true
		if len(question.Options) > 3 {
			return nil, fmt.Errorf("每个问题最多 3 个选项")
		}
		for _, option := range question.Options {
			if strings.TrimSpace(option.Label) == "" {
				return nil, fmt.Errorf("选项需要 label")
			}
		}
	}
	return request, nil
}

func (request *InputRequest) AnswerText(answers map[string]string) (string, error) {
	var text strings.Builder
	for _, question := range request.Questions {
		answer := strings.TrimSpace(answers[question.ID])
		if answer == "" {
			return "", fmt.Errorf("请回答：%s", question.Question)
		}
		fmt.Fprintf(&text, "%s\n%s\n\n", question.Question, answer)
	}
	return strings.TrimSpace(text.String()), nil
}
