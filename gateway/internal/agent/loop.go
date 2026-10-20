// Package agent is the pi-shaped loop: build request → stream model → run tool calls → append results → repeat
// until the model returns plain text. All EEG logic lives in the four hooks; the loop itself has none.
package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/skills"
)

const DefaultMaxTurns = 32

var ErrTurnBudget = errors.New("已达到本轮执行轮数上限")

// Event is what the loop emits over SSE. Names follow pi-agent-core with EEG additions.
type Event struct {
	Type           string          `json:"type"`
	Timestamp      time.Time       `json:"ts"`
	MessageID      string          `json:"message_id,omitempty"`
	RunID          string          `json:"run_id,omitempty"`
	Sequence       uint64          `json:"sequence,omitempty"`
	Text           string          `json:"text,omitempty"`
	ToolCallID     string          `json:"tool_call_id,omitempty"`
	ToolName       string          `json:"tool_name,omitempty"`
	Args           json.RawMessage `json:"args,omitempty"`
	Result         json.RawMessage `json:"result,omitempty"`
	IsError        bool            `json:"is_error,omitempty"`
	Skill          string          `json:"skill,omitempty"`
	Reason         string          `json:"reason,omitempty"`
	Turn           int             `json:"turn,omitempty"`
	Phase          string          `json:"phase,omitempty"`
	CitationIDs    []string        `json:"citation_ids,omitempty"`
	ArgumentsError string          `json:"arguments_error,omitempty"`
	RawArguments   string          `json:"raw_arguments,omitempty"`
}

// Hooks are the only extension points. Any of them may be nil.
type Hooks struct {
	// PrepareRequest may replace the system prompt (e.g. inject memory/RAG) before every model request.
	PrepareRequest func(ctx context.Context, s *Session, system *string) error
	// BeforeTool validates a call; returning (blocked=true, reason) turns the call into an error result for the model.
	BeforeTool func(ctx context.Context, s *Session, call ToolCall) (blocked bool, reason string)
	// AfterTool may rewrite the result (truncate, attach checks) before it is appended.
	AfterTool func(ctx context.Context, s *Session, call ToolCall, result json.RawMessage, isErr bool) (json.RawMessage, bool)
	// FinishTurn decides whether to force one more model request after a text-only answer.
	FinishTurn func(ctx context.Context, s *Session, answer string) (cont bool, injected string)
}

type ToolCall struct {
	ID             string
	Name           string
	Args           json.RawMessage
	ArgumentsError string
}

// ToolExecutor runs a named tool with JSON args (the worker client in production).
type ToolExecutor interface {
	Call(ctx context.Context, name string, args json.RawMessage) (json.RawMessage, error)
}

type ProgressToolExecutor interface {
	CallWithProgress(context.Context, string, json.RawMessage, func(json.RawMessage)) (json.RawMessage, error)
}

// Session is the linear transcript plus state the hooks need.
type Session struct {
	ID               string
	Title            string
	ParentSessionID  string
	DeletedAt        *time.Time
	Mode             contracts.PermissionMode
	Messages         []llm.Message // does not include the system message
	ArchivedMessages []llm.Message // complete transcript removed from model context by compaction
	Manifests        []*contracts.Manifest
	StagedManifests  []*contracts.Manifest // uploaded drafts, excluded from model context until submitted
	Previews         map[string]json.RawMessage
	Artifacts        []json.RawMessage
	ScratchFiles     []string                   // files scripts wrote into this session's scratch dir
	Knowledge        []json.RawMessage          // chunks retrieved for the current query
	Sources          map[string]json.RawMessage // immutable excerpts available to this conversation
	KnowledgeFacts   []json.RawMessage          // graph facts retrieved for the current query
	KnowledgeQuery   string
	KnowledgeUpdated bool
	Summaries        []string // compaction summaries, oldest first
	Usage            []TurnUsage
	ActiveSkill      *skills.Skill
	ToolErrors       int // failed tool executions in the current run
	Turn             int
	CreatedAt        time.Time
	UpdatedAt        time.Time
	Running          bool
	LastError        string
	LastStopped      bool
	LLMModel         string
	WorkspaceID      string
	PendingInput     *InputRequest
	mu               sync.Mutex
	steerMu          sync.Mutex
	steer            []llm.Message
}

type TurnUsage struct {
	Turn             int      `json:"turn"`
	Kind             string   `json:"kind"`
	ElapsedMS        int64    `json:"elapsed_ms"`
	PromptTokens     int      `json:"prompt_tokens,omitempty"`
	CompletionTokens int      `json:"completion_tokens,omitempty"`
	CachedTokens     *int     `json:"cached_tokens,omitempty"`
	Completed        bool     `json:"completed"`
	VisibleTools     []string `json:"visible_tools,omitempty"`
	InputEstimate    int      `json:"input_tokens_estimate,omitempty"`
}

// Steer queues a user message to be injected after the current tool batch (pi's steering queue).
func (s *Session) Steer(text string, presentation *llm.InputPresentation) {
	s.steerMu.Lock()
	defer s.steerMu.Unlock()
	s.steer = append(s.steer, llm.Message{Role: "user", Content: text, Presentation: presentation})
}

func (s *Session) DrainSteering() []llm.Message {
	s.steerMu.Lock()
	defer s.steerMu.Unlock()
	out := s.steer
	s.steer = nil
	return out
}

// Compactor shrinks the transcript when it grows past the budget. Nil disables compaction.
type Compactor func(ctx context.Context, s *Session, emit func(Event)) error

type Config struct {
	LLM               *llm.Client
	Executor          ToolExecutor
	AllTools          map[string]contracts.ToolDefinition // worker tools by name
	Skills            []*skills.Skill
	SystemFn          func(s *Session) string // static prefix builder
	FileCtxFn         func(s *Session) string // dynamic suffix builder
	Hooks             Hooks
	Compact           Compactor
	Checkpoint        func(*Session) error // save complete transcript groups, never partial tool batches
	MaxTurns          int
	MaxToolsPer       int
	InputPresentation *llm.InputPresentation
	Ablations         []string
}

// Run processes one user message. An empty userText resumes a failed request's retained transcript.
func Run(ctx context.Context, cfg Config, s *Session, userText string, emit func(Event)) (string, error) {
	if cfg.MaxTurns == 0 {
		cfg.MaxTurns = DefaultMaxTurns
	}
	if cfg.MaxToolsPer == 0 {
		cfg.MaxToolsPer = 8
	}
	s.mu.Lock()
	if s.LLMModel == "" {
		s.LLMModel = cfg.LLM.Model
	}
	s.Running = true
	if len(cfg.Skills) == 0 {
		s.ActiveSkill = nil
	}
	s.LastError, s.LastStopped = "", false
	s.ToolErrors = 0
	defer func() { s.Running = false; s.UpdatedAt = time.Now(); s.mu.Unlock() }()
	if s.Title == "" {
		s.Title = firstLine(userText, 40)
	}
	if s.CreatedAt.IsZero() {
		s.CreatedAt = time.Now()
	}

	if userText != "" {
		s.PendingInput = nil
		s.Messages = append(s.Messages, llm.Message{Role: "user", Content: userText, Presentation: cfg.InputPresentation})
	}
	checkpoint := func() error {
		s.UpdatedAt = time.Now()
		if cfg.Checkpoint != nil {
			return cfg.Checkpoint(s)
		}
		return nil
	}
	if err := checkpoint(); err != nil {
		return "", fmt.Errorf("save conversation: %w", err)
	}
	emit(Event{Type: "agent_start", Timestamp: time.Now()})
	defer func() { emit(Event{Type: "agent_end", Timestamp: time.Now()}) }()

	var final string
	for turn := 1; turn <= cfg.MaxTurns; turn++ {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		s.Turn = turn
		emit(Event{Type: "turn_start", Timestamp: time.Now(), Turn: turn})

		system := cfg.SystemFn(s)
		if cfg.Hooks.PrepareRequest != nil {
			if err := cfg.Hooks.PrepareRequest(ctx, s, &system); err != nil {
				return "", err
			}
		}
		if cfg.Compact != nil {
			if err := cfg.Compact(ctx, s, emit); err != nil {
				return "", fmt.Errorf("compaction: %w", err)
			}
		}
		if s.KnowledgeUpdated {
			s.KnowledgeUpdated = false
			if b, err := json.Marshal(map[string]any{"chunks": s.Knowledge, "facts": s.KnowledgeFacts}); err == nil {
				emit(Event{Type: "knowledge_retrieved", Timestamp: time.Now(), Result: b, Text: s.KnowledgeQuery})
			}
		}
		msgs := requestMessages(cfg, s, system, userText == "")

		specs := visibleToolSpecs(cfg, s)

		started := time.Now()
		messageID, messageEmit := startMessage(emit)
		text, calls, usage, err := streamTurn(ctx, cfg.LLM, msgs, specs, messageEmit)
		s.recordUsage(turn, "analysis", started, usage, err == nil)
		row := &s.Usage[len(s.Usage)-1]
		row.InputEstimate = EstimateRequestTokens(msgs, specs)
		for _, spec := range specs {
			row.VisibleTools = append(row.VisibleTools, spec.Function.Name)
		}
		if err != nil {
			if text != "" {
				message := s.assistantMessage(text, "interrupted")
				message.MessageID = messageID
				s.Messages = append(s.Messages, message)
				messageEmit(Event{Type: "message_completed", Timestamp: time.Now(), Phase: message.Phase, CitationIDs: message.CitationIDs})
			}
			if errors.Is(err, llm.ErrOutputLimit) {
				s.Messages = append(s.Messages, llm.Message{Role: "user", Content: "<generation_limit>上次回答达到输出长度上限。根据已完成的工具结果继续完成用户请求，省略已说过的内容；需要长代码时分段保存再执行。</generation_limit>"})
				if err := checkpoint(); err != nil {
					return "", err
				}
				emit(Event{Type: "turn_end", Timestamp: time.Now(), Turn: turn})
				continue
			}
			emit(Event{Type: "error", Timestamp: time.Now(), Text: err.Error()})
			return "", err
		}

		if len(calls) == 0 {
			message := s.assistantMessage(text, "final_answer")
			message.MessageID = messageID
			s.Messages = append(s.Messages, message)
			injected := s.DrainSteering()
			var reflection string
			if len(injected) == 0 && cfg.Hooks.FinishTurn != nil {
				if cont, inject := cfg.Hooks.FinishTurn(ctx, s, text); cont && inject != "" {
					reflection = inject
					message.Phase = "commentary"
					s.Messages[len(s.Messages)-1].Phase = message.Phase
				}
			}
			messageEmit(Event{Type: "message_completed", Timestamp: time.Now(), Phase: message.Phase, CitationIDs: message.CitationIDs})
			final = text
			if len(injected) > 0 {
				for _, t := range injected {
					visible := t.Content
					if t.Presentation != nil {
						visible = t.Presentation.Text
					}
					emit(Event{Type: "steering_injected", Timestamp: time.Now(), Text: visible})
					s.Messages = append(s.Messages, t)
				}
				if err := checkpoint(); err != nil {
					return "", err
				}
				emit(Event{Type: "turn_end", Timestamp: time.Now(), Turn: turn})
				continue
			}
			if reflection != "" {
				emit(Event{Type: "reflection_created", Timestamp: time.Now(), Reason: reflection})
				s.Messages = append(s.Messages, llm.Message{Role: "user", Content: "<reflection>\n" + reflection + "\n</reflection>"})
				if err := checkpoint(); err != nil {
					return "", err
				}
				emit(Event{Type: "turn_end", Timestamp: time.Now(), Turn: turn})
				continue
			}
			if err := checkpoint(); err != nil {
				return "", err
			}
			emit(Event{Type: "turn_end", Timestamp: time.Now(), Turn: turn})
			return final, nil
		}

		asst := s.assistantMessage(text, "commentary")
		asst.MessageID = messageID
		for _, c := range calls {
			var tc llm.ToolCall
			tc.ID, tc.Type = c.ID, "function"
			tc.ArgumentsError = c.ArgumentsError
			tc.Function.Name, tc.Function.Arguments = c.Name, string(c.Args)
			asst.ToolCalls = append(asst.ToolCalls, tc)
		}
		s.Messages = append(s.Messages, asst)
		messageEmit(Event{Type: "message_completed", Timestamp: time.Now(), Phase: asst.Phase, CitationIDs: asst.CitationIDs})

		for i, c := range calls {
			if s.PendingInput != nil {
				result := json.RawMessage(`{"status":"awaiting_user","note":"等待用户回答，本调用未执行"}`)
				s.Messages = append(s.Messages, llm.Message{Role: "tool", ToolCallID: c.ID, Name: c.Name, Content: string(result)})
				continue
			}
			if i >= cfg.MaxToolsPer {
				result := errResult("tool_budget", fmt.Sprintf("本轮最多执行 %d 个工具；此调用未执行。请先使用已完成结果，再按需分批调用。", cfg.MaxToolsPer))
				s.Messages = append(s.Messages, llm.Message{Role: "tool", ToolCallID: c.ID, Name: c.Name, Content: string(result)})
				emit(Event{Type: "tool_execution_end", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Result: result, IsError: true})
				continue
			}
			if ctx.Err() != nil {
				s.Messages = append(s.Messages, llm.Message{Role: "tool", ToolCallID: c.ID, Name: c.Name, Content: string(errResult("stopped", "用户已停止执行"))})
				continue
			}
			var presentation json.RawMessage
			result, isErr := executeOne(ctx, cfg, s, c, func(event Event) {
				if event.Type == "tool_execution_end" {
					presentation = deliveryPresentation(event.Result)
				}
				messageEmit(event)
			})
			s.Messages = append(s.Messages, llm.Message{Role: "tool", ToolCallID: c.ID, Name: c.Name, Content: string(result), ToolPresentation: presentation})
			if isErr {
				s.ToolErrors++
			}
		}
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		if s.PendingInput != nil {
			if err := checkpoint(); err != nil {
				return "", err
			}
			emit(Event{Type: "turn_end", Timestamp: time.Now(), Turn: turn})
			return "", nil
		}
		for _, t := range s.DrainSteering() {
			visible := t.Content
			if t.Presentation != nil {
				visible = t.Presentation.Text
			}
			emit(Event{Type: "steering_injected", Timestamp: time.Now(), Text: visible})
			s.Messages = append(s.Messages, t)
		}
		if err := checkpoint(); err != nil {
			return "", err
		}
		emit(Event{Type: "turn_end", Timestamp: time.Now(), Turn: turn})
	}
	// Keep budget exhaustion recoverable; a summary cannot stand in for unfinished tool work.
	budgetErr := fmt.Errorf("%w（%d 轮）；已完成结果已保留，可继续处理未完成事项", ErrTurnBudget, cfg.MaxTurns)
	s.Messages = append(s.Messages, llm.Message{Role: "user", Content: "<reflection>\n本次执行达到轮数预算，工具已停止，任务尚未完成。只汇报工具已证实的已完成结果、未完成事项和实际存在的文件路径。明确这是未完成的部分结果，可以继续处理。\n</reflection>"})
	system := cfg.SystemFn(s)
	if cfg.Hooks.PrepareRequest != nil { // same knowledge/context injection as regular turns
		if err := cfg.Hooks.PrepareRequest(ctx, s, &system); err != nil {
			return "", errors.Join(budgetErr, err)
		}
	}
	if cfg.Compact != nil {
		if err := cfg.Compact(ctx, s, emit); err != nil {
			return "", errors.Join(budgetErr, fmt.Errorf("compaction: %w", err))
		}
	}
	msgs := requestMessages(cfg, s, system, false)
	started := time.Now()
	messageID, messageEmit := startMessage(emit)
	text, _, usage, err := streamTurn(ctx, cfg.LLM, msgs, nil, messageEmit)
	s.recordUsage(cfg.MaxTurns+1, "partial", started, usage, err == nil)
	s.Usage[len(s.Usage)-1].InputEstimate = EstimateRequestTokens(msgs, nil)
	if text != "" {
		message := s.assistantMessage(text, "interrupted")
		message.MessageID = messageID
		s.Messages = append(s.Messages, message)
		messageEmit(Event{Type: "message_completed", Timestamp: time.Now(), Phase: message.Phase, CitationIDs: message.CitationIDs})
	}
	return text, errors.Join(budgetErr, err, checkpoint())
}

func requestMessages(cfg Config, s *Session, system string, resuming bool) []llm.Message {
	msgs := make([]llm.Message, 0, len(s.Messages)+4)
	msgs = append(msgs, llm.Message{Role: "system", Content: system})
	if len(s.Summaries) > 0 {
		msgs = append(msgs, llm.Message{Role: "user", Content: "<compaction_summary>\n" + s.Summaries[len(s.Summaries)-1] + "\n</compaction_summary>"})
	}
	msgs = append(msgs, s.Messages...)
	latest := llm.Message{Role: "user", Content: "<latest_context>\n内部参考：当前工作区协议、文件与产物状态。继续处理最近的用户请求，按对话表达规则组织回复。\n" + cfg.FileCtxFn(s) + publicProgressContext(s) + "\n</latest_context>"}
	for _, manifest := range s.Manifests {
		if manifest.Modality == "image" {
			latest.Images = append(latest.Images, llm.ImageInput{Path: manifest.VisionPath, MediaType: manifest.MediaType, Name: manifest.OriginalName})
		}
	}
	msgs = append(msgs, latest)
	if resuming && len(s.Messages) > 0 && s.Messages[len(s.Messages)-1].Role == "assistant" {
		msgs = append(msgs, llm.Message{Role: "user", Content: "上一次回复中断了。请基于已完成的工具结果继续完成用户请求，不要重复执行已完成的步骤。"})
	}
	return msgs
}

// Public progress is model-authored content grounded in the current task and completed tools.
// Provider-supplied reasoning has its own transient stream, separate from conversation text.
func publicProgressContext(s *Session) string {
	planVisible := false
	for i := len(s.Messages) - 1; i >= 0; i-- {
		message := s.Messages[i]
		if message.Role == "user" && !strings.HasPrefix(strings.TrimSpace(message.Content), "<") {
			break
		}
		if message.Role == "assistant" && strings.TrimSpace(message.Content) != "" {
			planVisible = true
		}
	}
	for i := len(s.Messages) - 1; i >= 0 && s.Messages[i].Role == "tool"; i-- {
		switch s.Messages[i].Name {
		case "inspect_record", "preprocess", "clean_artifacts", "segment_signal", "extract_features", "analyze_signal", "workspace_quality", "run_python", "recognize_emotion", "run_model", "plot", "read_artifact", "export":
			return "\n<progress_context>\n最新一批分析结果已经返回。若还需继续执行，先用 1–2 句公开分析进展概括结果中的一项具体发现，以及它决定的下一步；出现错误时说明实际原因与修正动作，再调用下一批工具。读取 skill、查询目录等准备步骤无需逐条播报，已说过的计划不重复。任务已完成就直接给最终发现和产物。\n</progress_context>"
		}
	}
	if !planVisible {
		return "\n<progress_context>\n本次尚未发出公开工作计划。若需要继续多步分析，在第一批分析工具调用前先用 1–2 句公开工作计划说明要比较什么、怎样确认结果、将交付什么；读取 skill 或目录只是准备，不代替这段说明。已有结果直接复用。问候、简单解释或只需给已有结果时直接回答。\n</progress_context>"
	}
	return ""
}

func (s *Session) recordUsage(turn int, kind string, started time.Time, usage *llm.Usage, completed bool) {
	row := TurnUsage{Turn: turn, Kind: kind, ElapsedMS: time.Since(started).Milliseconds(), Completed: completed}
	if usage != nil {
		row.PromptTokens, row.CompletionTokens, row.CachedTokens = usage.PromptTokens, usage.CompletionTokens, usage.CachedTokens()
	}
	s.Usage = append(s.Usage, row)
}

func visibleToolSpecs(cfg Config, s *Session) []llm.ToolSpec {
	active := s.ActiveSkill
	if len(cfg.Skills) == 0 {
		active = nil
	}
	names := skills.VisibleTools(active)
	var specs []llm.ToolSpec
	for _, n := range names {
		if s.WorkspaceID == "" {
			switch n {
			case "workspace_files", "workspace_quality", "write_file", "edit_file", "save_to_workspace":
				continue
			}
		}
		if def, ok := cfg.AllTools[n]; ok {
			specs = append(specs, llm.NewToolSpec(def.Name, def.Description, def.InputSchema))
		}
	}
	// gateway-side tools
	if len(cfg.Skills) > 0 {
		specs = append(specs, llm.NewToolSpec("read_skill", "读取一个 skill 的完整说明。参数 name 取自 <available_skills>。读取后该 skill 被激活，作为本轮的方法建议。",
			json.RawMessage(`{"type":"object","properties":{"name":{"type":"string"},"reference":{"type":"string","description":"skill 内 references/ 下的引用路径，如 references/api.md；也支持省略目录的 api.md。路径必须留在该 references 目录内。不填时读取 SKILL.md 正文。"}},"required":["name"]}`)))
	}
	specs = append(specs, llm.NewToolSpec("request_user_input", "询问影响本次目标、结论或后续操作的关键疑问，包括数据缺项、分析范围、方法取舍和用户偏好。先使用已有信息与工具能查清的事实；常规步骤可自行决定，不重复询问或为普通知识问题弹窗。每次 1–3 个简短问题，实测参数用 number/text，分类偏好用 choice。调用后等待回答再继续。", json.RawMessage(inputRequestSchema)))
	sort.Slice(specs, func(i, j int) bool { return specs[i].Function.Name < specs[j].Function.Name })
	return specs
}

func deliveryPresentation(result json.RawMessage) json.RawMessage {
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(result, &fields)
	view := map[string]json.RawMessage{}
	for _, key := range []string{"workspace_file", "workspace_files", "result_links"} {
		if value, present := fields[key]; present {
			view[key] = value
		}
	}
	if len(view) == 0 {
		return nil
	}
	body, _ := json.Marshal(view)
	return body
}

func executeOne(ctx context.Context, cfg Config, s *Session, c ToolCall, emit func(Event)) (json.RawMessage, bool) {
	if c.ArgumentsError != "" {
		result := errResult("bad_args", c.ArgumentsError)
		emit(Event{Type: "tool_execution_end", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Result: result, IsError: true, ArgumentsError: c.ArgumentsError, RawArguments: string(c.Args)})
		return result, true
	}
	emit(Event{Type: "tool_execution_start", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Args: c.Args})
	var result json.RawMessage
	isErr := false

	if cfg.Hooks.BeforeTool != nil {
		if blocked, reason := cfg.Hooks.BeforeTool(ctx, s, c); blocked {
			result = errResult("blocked", reason)
			isErr = true
			emit(Event{Type: "tool_execution_end", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Result: result, IsError: true})
			return result, true
		}
	}

	switch c.Name {
	case "request_user_input":
		request, err := newInputRequest(c.ID, c.Args)
		if err != nil {
			result, isErr = errResult("bad_question", err.Error()), true
			break
		}
		s.PendingInput = request
		result, _ = json.Marshal(map[string]any{"status": "awaiting_user", "request": request})
		emit(Event{Type: "user_input_requested", Timestamp: time.Now(), ToolCallID: c.ID, Result: result})
	case "read_skill":
		var a struct {
			Name      string `json:"name"`
			Reference string `json:"reference"`
		}
		_ = json.Unmarshal(c.Args, &a)
		found := false
		for _, sk := range cfg.Skills {
			if sk.Name == a.Name {
				if sk.DisableModelInvocation {
					found = true
					result, isErr = errResult("manual_skill", "该 skill 仅允许用户通过 / 命令选择"), true
					break
				}
				s.ActiveSkill = sk
				found = true
				emit(Event{Type: "skill_activated", Timestamp: time.Now(), Skill: sk.Name})
				var recommended, unavailable []string
				for _, name := range skills.VisibleTools(sk) {
					if _, ok := cfg.AllTools[name]; ok {
						recommended = append(recommended, name)
					} else {
						unavailable = append(unavailable, name)
					}
				}
				instructions, err := sk.Read(a.Reference)
				if err != nil {
					result, isErr = errResult("skill_reference", err.Error()), true
					break
				}
				result, _ = json.Marshal(map[string]any{"status": "ok", "skill": sk.Name, "instructions": instructions,
					"recommended_tools": recommended, "unavailable_tools": unavailable})
			}
		}
		if !found {
			result, isErr = errResult("unknown_skill", "no skill named "+a.Name), true
		}
	default:
		if _, ok := cfg.AllTools[c.Name]; !ok {
			result, isErr = errResult("unknown_tool", "tool not visible or not registered: "+c.Name), true
			break
		}
		args := withSession(c.Args, s.ID)
		if s.WorkspaceID != "" {
			var input map[string]json.RawMessage
			json.Unmarshal(args, &input)
			input["_workspace_id"], _ = json.Marshal(s.WorkspaceID)
			args, _ = json.Marshal(input)
		}
		var body json.RawMessage
		var err error
		if executor, ok := cfg.Executor.(ProgressToolExecutor); ok && (c.Name == "run_model" || c.Name == "recognize_emotion" || c.Name == "workspace_quality" || c.Name == "analyze_signal" || c.Name == "run_python" || c.Name == "clean_artifacts") {
			body, err = executor.CallWithProgress(ctx, c.Name, args, func(progress json.RawMessage) {
				emit(Event{Type: "tool_execution_progress", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Result: progress})
			})
		} else {
			body, err = cfg.Executor.Call(ctx, c.Name, args)
		}
		if err != nil {
			isErr = true
			if len(body) > 0 && json.Valid(body) {
				result = body
			} else {
				result = errResult("worker_error", err.Error())
			}
		} else {
			result = body
		}
	}

	presentation := result
	if cfg.Hooks.AfterTool != nil {
		result, isErr = cfg.Hooks.AfterTool(ctx, s, c, result, isErr)
	}
	if isErr && c.Name != "run_python" {
		presentation = result
	}
	emit(Event{Type: "tool_execution_end", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Result: presentation, IsError: isErr})
	if !isErr || c.Name == "run_python" {
		var out struct {
			Artifact  json.RawMessage   `json:"artifact"`
			Artifacts []json.RawMessage `json:"artifacts"`
		}
		if json.Unmarshal(presentation, &out) == nil {
			if len(out.Artifact) > 2 {
				emit(Event{Type: "artifact_ready", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Result: out.Artifact})
			}
			for _, a := range out.Artifacts {
				if len(a) > 2 {
					emit(Event{Type: "artifact_ready", Timestamp: time.Now(), ToolCallID: c.ID, ToolName: c.Name, Result: a})
				}
			}
		}
	}
	return result, isErr
}

// withSession adds the hidden _session_id argument so the worker can scope scratch dirs and file roots.
func withSession(args json.RawMessage, sessionID string) json.RawMessage {
	if sessionID == "" {
		return args
	}
	var m map[string]json.RawMessage
	if json.Unmarshal(args, &m) != nil {
		return args
	}
	if m == nil {
		m = map[string]json.RawMessage{}
	}
	m["_session_id"], _ = json.Marshal(sessionID)
	out, err := json.Marshal(m)
	if err != nil {
		return args
	}
	return out
}

func errResult(code, msg string) json.RawMessage {
	b, _ := json.Marshal(map[string]any{"status": "error", "error": map[string]any{"code": code, "message": msg, "recoverable": true}})
	return b
}

func firstLine(s string, max int) string {
	s = strings.TrimSpace(strings.SplitN(s, "\n", 2)[0])
	r := []rune(s)
	if len(r) > max {
		return string(r[:max]) + "…"
	}
	return s
}

func startMessage(emit func(Event)) (string, func(Event)) {
	id := fmt.Sprintf("msg_%d", time.Now().UnixNano())
	messageEmit := func(event Event) {
		event.MessageID = id
		emit(event)
	}
	// A chat-completions provider cannot identify text as commentary until it also emits a tool call.
	messageEmit(Event{Type: "message_started", Timestamp: time.Now(), Phase: "pending"})
	return id, messageEmit
}

// streamTurn runs one model request, forwarding public content deltas and assembling tool calls.
func streamTurn(ctx context.Context, c *llm.Client, msgs []llm.Message, tools []llm.ToolSpec, emit func(Event)) (string, []ToolCall, *llm.Usage, error) {
	ch := make(chan llm.Event, 64)
	go c.Stream(ctx, msgs, tools, ch)
	var text strings.Builder
	type partial struct {
		id, name  string
		args      strings.Builder
		announced bool
	}
	parts := map[int]*partial{}
	var order []int
	var usage *llm.Usage
	var finishReason string
	thinking := false
	for ev := range ch {
		switch ev.Type {
		case "text_delta":
			text.WriteString(ev.Text)
			emit(Event{Type: "message_delta", Timestamp: time.Now(), Text: ev.Text})
		case "reasoning_delta":
			if !thinking {
				thinking = true
				emit(Event{Type: "model_activity", Timestamp: time.Now(), Phase: "thinking"})
			}
			emit(Event{Type: "reasoning_delta", Timestamp: time.Now(), Text: ev.Text})
		case "tool_call_delta":
			if len(order) == 0 {
				emit(Event{Type: "message_phase", Timestamp: time.Now(), Phase: "commentary"})
			}
			p, ok := parts[ev.Index]
			if !ok {
				p = &partial{}
				parts[ev.Index] = p
				order = append(order, ev.Index)
			}
			if ev.ID != "" {
				p.id = ev.ID
			}
			if ev.Name != "" {
				p.name = ev.Name
			}
			p.args.WriteString(ev.Args)
			if !p.announced && p.id != "" && p.name != "" {
				p.announced = true
				emit(Event{Type: "tool_call_started", Timestamp: time.Now(), ToolCallID: p.id, ToolName: p.name})
			}
		case "error":
			return text.String(), nil, usage, ev.Err
		case "done":
			usage = ev.Usage
			finishReason = ev.FinishReason
		}
	}
	var calls []ToolCall
	for i, idx := range order {
		p := parts[idx]
		id := p.id
		if id == "" {
			id = fmt.Sprintf("call_%d_%d", time.Now().UnixNano(), i)
		}
		args := strings.TrimSpace(p.args.String())
		if args == "" {
			args = "{}"
		}
		argumentsError := llm.ToolArgumentsError(json.RawMessage(args))
		if finishReason == "length" {
			argumentsError = "本轮工具调用达到模型输出长度上限，调用未执行。请缩短参数；长脚本分段保存后执行，再重新生成完整调用。"
		}
		calls = append(calls, ToolCall{ID: id, Name: p.name, Args: json.RawMessage(args), ArgumentsError: argumentsError})
	}
	if finishReason == "length" && len(calls) == 0 {
		return text.String(), nil, usage, llm.ErrOutputLimit
	}
	if len(calls) == 0 && strings.TrimSpace(text.String()) == "" {
		return "", nil, usage, fmt.Errorf("模型结束了请求，但没有返回回答或工具调用，请重试")
	}
	return text.String(), calls, usage, nil
}
