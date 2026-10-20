package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"eegagent/gateway/internal/llm"
)

// Linear compaction (PRD §12.1, v1). When the transcript exceeds the token budget, everything except the
// last keepTail messages is summarized by one separate LLM call using the EEG template, then replaced.
// Artifact and manifest ids must survive verbatim; the prompt says so and the hook keeps runtime_context anyway.

const summarySystem = `你负责给后续分析留下简洁的内部交接摘要。按下面结构记录与当前任务有关的事实，空章节省略。
manifest id（sig_…）、artifact id（art_…）、关键通道名、时间范围和数值原样保留。用户明确提供的元数据、已经回答的问题和最新修正优先于早先推断。
摘要用于继续工作，后续回复按当前问题重新组织；保留用户的语言、篇幅和表达偏好，已经解决的问题记录解决结果。当前能力以现行工具和 skill 为准。

## 目标
## 数据事实
当前记录的 id、通道、采样率、时长、单位及用户补充的信息；有用的质量结果和检查范围
## 已生成产物
artifact id、kind、时间范围、一句话结论
情绪评阅保留 emotion_assessment 的 id、assessment_status、实际标签或未评定状态、时段与主要依据
## 模型运行
实际模型、窗口策略与运行结果
## 当前待确认项
仅保留仍会改变当前任务的缺项
## 决策
为什么选这个方法/特征、实际得到的判断以及已经解决的问题
## 进度与下一步
## 用户偏好
本会话内用户表达的频带、输出格式、语言和篇幅偏好`

// EstimateTokens is a cheap mixed-script estimate: CJK ≈ 1 token/char, Latin ≈ 1 token/4 chars.
func EstimateTokens(msgs []llm.Message) int {
	n := 0
	for _, m := range msgs {
		n += estimate(m.Content)
		for _, tc := range m.ToolCalls {
			n += estimate(tc.Function.Arguments) + 8
		}
	}
	return n
}

// EstimateRequestTokens counts the actual message projection and tool definitions.
func EstimateRequestTokens(msgs []llm.Message, specs []llm.ToolSpec) int {
	n := EstimateTokens(msgs)
	for _, spec := range specs {
		body, _ := json.Marshal(spec)
		n += estimate(string(body))
	}
	return n
}

func ContextTokens(cfg Config, s *Session) int {
	return EstimateRequestTokens(requestMessages(cfg, s, cfg.SystemFn(s), false), visibleToolSpecs(cfg, s))
}

func estimate(s string) int {
	cjk, other := 0, 0
	for _, r := range s {
		if r >= 0x2E80 {
			cjk++
		} else {
			other++
		}
	}
	return cjk + other/4
}

// DefaultCompactor returns a Compactor that triggers above maxTokens and keeps the last keepTail messages.
func DefaultCompactor(client *llm.Client, maxTokens, keepTail int, requestSize func(*Session) int) Compactor {
	if maxTokens <= 0 {
		maxTokens = 60000
	}
	if keepTail <= 0 {
		keepTail = 8
	}
	return func(ctx context.Context, s *Session, emit func(Event)) error {
		if len(s.Messages) <= keepTail+2 {
			return nil
		}
		total := requestSize(s)
		if total < maxTokens {
			return nil
		}
		cut := len(s.Messages) - keepTail
		// never cut inside an assistant(tool_calls)/tool group: move the cut back to a user or plain assistant message
		for cut > 0 && (s.Messages[cut].Role == "tool" || (s.Messages[cut].Role == "assistant" && len(s.Messages[cut].ToolCalls) > 0)) {
			cut--
		}
		if cut < 2 {
			return nil
		}
		emit(Event{Type: "compaction_start", Timestamp: time.Now(), Text: fmt.Sprintf("%d tokens, compacting %d messages", total, cut)})

		var prev string
		if len(s.Summaries) > 0 {
			prev = "之前的摘要：\n" + s.Summaries[len(s.Summaries)-1] + "\n\n"
		}
		req := []llm.Message{
			{Role: "system", Content: summarySystem},
			{Role: "user", Content: prev + "对话记录：\n" + serialize(s.Messages[:cut]) + "\n\n请输出摘要。"},
		}
		started := time.Now()
		text, _, usage, err := streamTurn(ctx, client.WithModel(s.LLMModel), req, nil, func(Event) {})
		s.recordUsage(0, "compaction", started, usage, err == nil)
		if err != nil {
			return err
		}
		s.Summaries = append(s.Summaries, strings.TrimSpace(text))
		s.ArchivedMessages = append(s.ArchivedMessages, s.Messages[:cut]...)
		s.Messages = append([]llm.Message{}, s.Messages[cut:]...)
		emit(Event{Type: "compaction_end", Timestamp: time.Now(), Text: fmt.Sprintf("kept %d messages", len(s.Messages))})
		return nil
	}
}

const toolResultMaxChars = 2000 // same value as pi's serializeConversation

func serialize(msgs []llm.Message) string {
	var b strings.Builder
	for _, m := range msgs {
		switch m.Role {
		case "user":
			b.WriteString("[User]: " + m.Content + "\n\n")
		case "assistant":
			if m.Content != "" {
				b.WriteString("[Assistant]: " + m.Content + "\n\n")
			}
			if len(m.ToolCalls) > 0 {
				var calls []string
				for _, tc := range m.ToolCalls {
					calls = append(calls, tc.Function.Name+"("+tc.Function.Arguments+")")
				}
				b.WriteString("[Assistant tool calls]: " + strings.Join(calls, "; ") + "\n\n")
			}
		case "tool":
			c := m.Content
			if len(c) > toolResultMaxChars {
				c = c[:toolResultMaxChars] + fmt.Sprintf("\n[... %d more characters truncated]", len(m.Content)-toolResultMaxChars)
			}
			b.WriteString("[Tool result " + m.Name + "]: " + c + "\n\n")
		}
	}
	return b.String()
}
