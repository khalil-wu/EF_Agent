package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
)

func TestImageModelChoiceIsExplicitAndPrecedesConversationCommit(t *testing.T) {
	calls := 0
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/models" {
			fmt.Fprint(w, `{"data":[{"id":"text-model"},{"id":"vision-model"}]}`)
			return
		}
		calls++
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"红、蓝、黄、绿\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer provider.Close()
	root := t.TempDir()
	path := filepath.Join(root, "fixture.png")
	if err := os.WriteFile(path, []byte("synthetic image transport; pixel encoding covered by image_input_test"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := agent.Config{LLM: llm.New(provider.URL, "", "text-model"), SystemFn: func(*agent.Session) string { return "SYS" }, FileCtxFn: func(*agent.Session) string { return "CTX" }}
	s := New(cfg, nil, root)
	s.ModelName = "text-model"
	s.ModelCapabilities = map[string]llm.ModelCapability{"vision-model": {ImageInput: true}}
	h := s.Routes()
	id := create(t, h)
	s.sessions[id].StagedManifests = []*contracts.Manifest{{ManifestID: "img_fixture", Modality: "image", VisionPath: path, SourcePath: path, MediaType: "image/png"}}
	body := `{"text":"图片是什么？","attachments":["img_fixture"]}`
	blocked := request(t, h, "POST", "/sessions/"+id+"/messages", body)
	if blocked.Code != 400 || !strings.Contains(blocked.Body.String(), "image_model_required") || calls != 0 || len(s.sessions[id].Messages) != 0 || len(s.sessions[id].StagedManifests) != 1 {
		t.Fatalf("unverified image input was sent or committed: %d %s calls=%d", blocked.Code, blocked.Body.String(), calls)
	}
	selected := request(t, h, "PATCH", "/sessions/"+id+"/model", `{"model":"vision-model"}`)
	if selected.Code != 200 {
		t.Fatal(selected.Body.String())
	}
	completed := request(t, h, "POST", "/sessions/"+id+"/messages", body)
	if completed.Code != 200 || calls != 1 || len(s.sessions[id].Manifests) != 1 {
		t.Fatal(completed.Code, completed.Body.String(), calls)
	}
	back := request(t, h, "PATCH", "/sessions/"+id+"/model", `{"model":"text-model"}`)
	if back.Code != 400 || s.sessions[id].LLMModel != "vision-model" {
		t.Fatal(back.Code, back.Body.String())
	}
	models := request(t, h, "GET", "/llm/models", "")
	if !strings.Contains(models.Body.String(), `"image_input":true`) {
		t.Fatal(models.Body.String())
	}
}
