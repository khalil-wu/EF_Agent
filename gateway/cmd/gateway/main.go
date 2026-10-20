// Command gateway starts the EFAgent gateway: loads skills and project context, discovers worker tools,
// wires knowledge retrieval, serves HTTP + SSE + the web UI.
//
// Env:
//
//	EEGAGENT_LLM_BASE_URL     e.g. https://api.deepseek.com/v1 or http://localhost:8000/v1
//	EEGAGENT_LLM_API_KEY
//	EEGAGENT_LLM_MODEL        e.g. deepseek-chat, qwen-plus
//	EEGAGENT_LLM_MAX_TOKENS   default 16384; maximum output tokens per request
//	EEGAGENT_LLM_PROXY        environment (default) or direct; affects only model requests
//	EEGAGENT_WORKER_URL       default http://localhost:8100
//	EEGAGENT_SKILLS_DIR       default ../skills
//	EEGAGENT_STORAGE_DIR      default ../storage  (must equal the worker's storage dir)
//	EEGAGENT_DATA_DIRS        ';'-separated data dirs (also searched for AGENTS.md / EEGAGENT.md)
//	EEGAGENT_PROJECT_DIRS     ';'-separated dirs to search for AGENTS.md / EEGAGENT.md (default: repo root + data dirs)
//	EEGAGENT_ADDR             default :8080
//	EEGAGENT_MAX_TURNS        default 32
//	EEGAGENT_COMPACT_TOKENS   default 60000;  EEGAGENT_COMPACT_KEEP default 8
//	EEGAGENT_ABLATION         comma list: shared_tools,focused_tools,no_skills,no_retrieval,no_requires,no_compat_context
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/hooks"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/prompt"
	"eegagent/gateway/internal/server"
	"eegagent/gateway/internal/skills"
	"eegagent/gateway/internal/worker"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		var n int
		if _, err := fmt.Sscanf(v, "%d", &n); err == nil {
			return n
		}
	}
	return def
}

func splitDirs(v string) []string {
	var out []string
	for _, d := range strings.Split(v, ";") {
		if d = strings.TrimSpace(d); d != "" {
			out = append(out, d)
		}
	}
	return out
}

func main() {
	baseURL := env("EEGAGENT_LLM_BASE_URL", "")
	model := env("EEGAGENT_LLM_MODEL", "")
	if baseURL == "" || model == "" {
		log.Fatal("set EEGAGENT_LLM_BASE_URL and EEGAGENT_LLM_MODEL")
	}
	skillsDir := env("EEGAGENT_SKILLS_DIR", filepath.Join("..", "skills"))
	storageDir, _ := filepath.Abs(env("EEGAGENT_STORAGE_DIR", filepath.Join("..", "storage")))
	workerURL := env("EEGAGENT_WORKER_URL", "http://localhost:8100")

	sk, err := skills.Load(skillsDir)
	if err != nil {
		log.Fatalf("load skills: %v", err)
	}
	for _, s := range sk {
		for _, w := range s.Warnings {
			log.Printf("skill %s: %s", s.Name, w)
		}
	}
	log.Printf("loaded %d skills from %s", len(sk), skillsDir)

	projectDirs := splitDirs(env("EEGAGENT_PROJECT_DIRS", ""))
	if len(projectDirs) == 0 {
		projectDirs = append([]string{".."}, splitDirs(os.Getenv("EEGAGENT_DATA_DIRS"))...)
	}
	project := prompt.LoadProjectContext(projectDirs)
	for _, f := range project {
		log.Printf("project context: %s", f.Path)
	}

	wc := worker.New(workerURL)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	defs, err := wc.Tools(ctx)
	if err != nil {
		log.Fatalf("worker %s unreachable: %v", workerURL, err)
	}
	all := map[string]contracts.ToolDefinition{}
	for _, d := range defs {
		all[d.Name] = d
		skills.AllToolNames = append(skills.AllToolNames, d.Name)
	}
	log.Printf("worker exposes %d tools", len(all))

	ablation, err := parseAblations(os.Getenv("EEGAGENT_ABLATION"))
	if err != nil {
		log.Fatal(err)
	}
	for _, name := range ablationNames(ablation) {
		log.Printf("analysis configuration: %s", name)
	}
	skills.FocusedTools = ablation["focused_tools"]
	skills.ExposeAllTools = !skills.FocusedTools
	if ablation["no_skills"] {
		sk = nil
	}
	if ablation["no_retrieval"] {
		for _, name := range []string{"search_knowledge", "knowledge_graph", "read_document"} {
			delete(all, name)
		}
	}

	var retriever hooks.Retriever
	if _, ok := all["search_knowledge"]; ok {
		retriever = func(ctx context.Context, q string, k int, sessionID string) (*hooks.Retrieval, error) {
			args, _ := json.Marshal(map[string]any{"query": q, "k": k, "_session_id": sessionID})
			body, err := wc.Call(ctx, "search_knowledge", args)
			if err != nil {
				return nil, err
			}
			var out hooks.Retrieval
			if err := json.Unmarshal(body, &out); err != nil {
				return nil, err
			}
			return &out, nil
		}
	}

	llmClient := llm.New(baseURL, os.Getenv("EEGAGENT_LLM_API_KEY"), model)
	llmClient.MaxTokens = envInt("EEGAGENT_LLM_MAX_TOKENS", llm.DefaultMaxTokens)
	if llmClient.MaxTokens <= 0 {
		log.Fatal("EEGAGENT_LLM_MAX_TOKENS must be positive")
	}
	proxyMode := env("EEGAGENT_LLM_PROXY", "environment")
	switch proxyMode {
	case "direct":
		llmClient.HTTP.Transport.(*http.Transport).Proxy = nil
	case "environment":
	default:
		log.Fatal("EEGAGENT_LLM_PROXY must be environment or direct")
	}
	log.Printf("model=%s proxy=%s", model, proxyMode)
	hk := hooks.Build(all, storageDir, retriever)
	if ablation["no_requires"] {
		hk.BeforeTool = hooks.VisibilityOnly(all)
	}
	cfg := agent.Config{
		LLM:      llmClient,
		Executor: wc,
		AllTools: all,
		Skills:   sk,
		SystemFn: func(s *agent.Session) string {
			return prompt.StaticPrefix(prompt.PrefixOptions{Skills: sk, Active: s.ActiveSkill, Mode: s.Mode, ToolDefs: all, Project: project})
		},
		FileCtxFn: func(s *agent.Session) string {
			ms := s.Manifests
			if ablation["no_compat_context"] {
				ms = prompt.StripCompat(ms)
			}
			// Read the session's current workspace and its protocol on each request, including after a switch or file edit.
			parts := []string{prompt.FileContext(ms), prompt.WorkspaceContext(storageDir, s.WorkspaceID)}
			if rc := prompt.RuntimeContext(s.Artifacts, s.ScratchFiles); rc != "" {
				parts = append(parts, rc)
			}
			if !ablation["no_retrieval"] {
				if kc := prompt.KnowledgeContext(s.Knowledge, s.KnowledgeFacts); kc != "" {
					parts = append(parts, kc)
				}
			}
			if s.ActiveSkill != nil && !ablation["no_skills"] {
				loaded := false
				for _, m := range s.Messages {
					if m.Role == "tool" && m.Name == "read_skill" {
						var result struct{ Skill, Instructions string }
						if json.Unmarshal([]byte(m.Content), &result) == nil && result.Skill == s.ActiveSkill.Name && strings.TrimSpace(result.Instructions) == strings.TrimSpace(s.ActiveSkill.Body()) {
							loaded = true
							break
						}
					}
				}
				if !loaded {
					parts = append(parts, fmt.Sprintf("<active_skill name=%q>\n%s\n</active_skill>", s.ActiveSkill.Name, strings.TrimSpace(s.ActiveSkill.Body())))
				}
			}
			parts = append(parts, "<reply_format>\n"+prompt.ReplyFormat+"\n</reply_format>")
			return strings.Join(parts, "\n\n")
		},
		Hooks:     hk,
		MaxTurns:  envInt("EEGAGENT_MAX_TURNS", agent.DefaultMaxTurns),
		Ablations: ablationNames(ablation),
	}
	cfg.Compact = agent.DefaultCompactor(llmClient, envInt("EEGAGENT_COMPACT_TOKENS", 60000), envInt("EEGAGENT_COMPACT_KEEP", 8), func(s *agent.Session) int { return agent.ContextTokens(cfg, s) })

	srv := server.New(cfg, wc, storageDir)
	srv.ModelName = model
	srv.ModelProxy = proxyMode
	srv.BuildID = env("EEGAGENT_GATEWAY_BUILD_ID", env("EEGAGENT_BUILD_ID", "development"))
	srv.ConfigID = os.Getenv("EEGAGENT_GATEWAY_CONFIG_ID")
	srv.SourceRoot, _ = filepath.Abs(env("EEGAGENT_SOURCE_ROOT", ".."))
	srv.ModelCapabilities, err = llm.LoadCapabilities(env("EEGAGENT_LLM_CAPABILITIES", filepath.Join("..", "configs", "conversation_models.json")))
	if err != nil {
		log.Fatalf("load conversation model capabilities: %v", err)
	}
	addr := env("EEGAGENT_ADDR", ":8080")
	displayAddr := addr
	if strings.HasPrefix(displayAddr, ":") {
		displayAddr = "localhost" + displayAddr
	}
	fmt.Printf("EFAgent gateway on http://%s (model=%s, worker=%s)\n", displayAddr, model, workerURL)
	log.Fatal(http.ListenAndServe(addr, srv.Routes()))
}
