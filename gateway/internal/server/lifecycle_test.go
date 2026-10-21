package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strings"
	"testing"
	"time"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/worker"
)

func request(t *testing.T, h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(method, path, strings.NewReader(body)))
	return w
}

func create(t *testing.T, h http.Handler) string {
	t.Helper()
	w := request(t, h, "POST", "/sessions", "")
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	var j struct {
		ID string `json:"session_id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &j); err != nil {
		t.Fatal(err)
	}
	return j.ID
}

type observedResponse struct {
	*httptest.ResponseRecorder
	delta chan struct{}
}

func (w *observedResponse) Write(body []byte) (int, error) {
	n, err := w.ResponseRecorder.Write(body)
	if strings.Contains(string(body), "event: message_delta\n") {
		select {
		case w.delta <- struct{}{}:
		default:
		}
	}
	return n, err
}

func TestNewSessionSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	s := New(agent.Config{}, nil, dir)
	id := create(t, s.Routes())
	restarted := New(agent.Config{}, nil, dir).Routes()
	if w := request(t, restarted, "GET", "/sessions/"+id, ""); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if w := request(t, restarted, "GET", "/sessions", ""); !strings.Contains(w.Body.String(), id) {
		t.Fatal(w.Body.String())
	}
	if w := request(t, restarted, "POST", "/sessions/"+id+"/messages", `{"text":"hi","mode":"invalid"}`); w.Code != 400 {
		t.Fatal(w.Code)
	}
}

func TestRunningSessionSnapshotSteeringAndStop(t *testing.T) {
	started := make(chan struct{})
	modelCalls := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		modelCalls++
		w.Header().Set("Content-Type", "text/event-stream")
		if modelCalls > 1 {
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"continued answer\"}}]}\n\ndata: [DONE]\n\n")
			return
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"partial answer\"}}]}\n\n")
		w.(http.Flusher).Flush()
		close(started)
		<-r.Context().Done()
	}))
	defer model.Close()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	dir := t.TempDir()
	s := New(cfg, nil, dir)
	h := s.Routes()
	id := create(t, h)
	done := make(chan *httptest.ResponseRecorder, 1)
	delta := make(chan struct{}, 1)
	go func() {
		w := &observedResponse{ResponseRecorder: httptest.NewRecorder(), delta: delta}
		h.ServeHTTP(w, httptest.NewRequest("POST", "/sessions/"+id+"/messages", strings.NewReader(`{"text":"hello"}`)))
		done <- w.ResponseRecorder
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("model did not start")
	}
	select {
	case <-delta:
	case <-time.After(5 * time.Second):
		t.Fatal("partial answer was not delivered")
	}
	for i := 0; i < 10; i++ {
		w := request(t, h, "GET", "/sessions/"+id, "")
		if w.Code != 200 || !strings.Contains(w.Body.String(), `"running":true`) {
			t.Fatal(w.Body.String())
		}
	}
	if w := request(t, h, "POST", "/sessions/"+id+"/messages", `{"text":"queued note"}`); w.Code != 202 {
		t.Fatal(w.Body.String())
	}
	if w := request(t, h, "POST", "/sessions/"+id+"/attachments", ""); w.Code != 409 {
		t.Fatal(w.Body.String())
	}
	if w := request(t, h, "POST", "/sessions/"+id+"/stop", ""); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	select {
	case w := <-done:
		if !strings.Contains(w.Body.String(), "event: stopped") || strings.Contains(w.Body.String(), "event: error") || !strings.Contains(w.Body.String(), "event: session_saved") {
			t.Fatal(w.Body.String())
		}
	case <-time.After(5 * time.Second):
		t.Fatal("stop did not finish")
	}
	saved, err := session.Load(dir, id, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !saved.LastStopped {
		t.Fatal("stop not persisted")
	}
	content := ""
	for _, m := range saved.Messages {
		content += m.Content
	}
	if !strings.Contains(content, "partial answer") || !strings.Contains(content, "queued note") {
		t.Fatal(content)
	}
	if w := request(t, h, "GET", "/sessions/"+id, ""); !strings.Contains(w.Body.String(), `"running":false`) {
		t.Fatal(w.Body.String())
	}
	resumed := request(t, h, "POST", "/sessions/"+id+"/messages", `{"retry":true}`)
	if resumed.Code != 200 || !strings.Contains(resumed.Body.String(), "continued answer") {
		t.Fatal(resumed.Body.String())
	}
	continued, err := session.Load(dir, id, nil)
	if err != nil {
		t.Fatal(err)
	}
	users := 0
	for _, message := range continued.Messages {
		if message.Role == "user" && message.Content == "hello" {
			users++
		}
	}
	if users != 1 || continued.LastStopped || continued.LastError != "" {
		t.Fatalf("continuation duplicated input or retained terminal state: %+v", continued)
	}
}

func TestWorkerFailureIsVisibleAndAssetsAreLocal(t *testing.T) {
	down := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "unavailable", 503) }))
	defer down.Close()
	h := New(agent.Config{}, worker.New(down.URL), t.TempDir()).Routes()
	w := request(t, h, "GET", "/health", "")
	if !strings.Contains(w.Body.String(), `"worker":"unavailable"`) {
		t.Fatal(w.Body.String())
	}
	for _, path := range []string{"/", "/static/app.css", "/static/app.js"} {
		w = request(t, h, "GET", path, "")
		b, _ := io.ReadAll(w.Result().Body)
		if w.Code != 200 || len(b) == 0 {
			t.Fatalf("%s: %d", path, w.Code)
		}
	}
}

func TestPageBindsEveryStaticAssetToTheRunningBuildAndConfiguration(t *testing.T) {
	references := regexp.MustCompile(`(?:src|href)="(/static/[^"]+)"`)
	previous := map[string]string{}
	for _, identity := range []struct{ build, config string }{{"build-one", "fonts-one"}, {"build-two", "fonts-one"}, {"build-two", "fonts-two"}} {
		s := New(agent.Config{}, nil, t.TempDir())
		s.BuildID, s.ConfigID = identity.build, identity.config
		h := s.Routes()
		page := request(t, h, "GET", "/", "")
		if page.Code != 200 || page.Header().Get("Cache-Control") != "no-cache" {
			t.Fatalf("page: %d %v", page.Code, page.Header())
		}
		matches := references.FindAllStringSubmatch(page.Body.String(), -1)
		if len(matches) < 10 {
			t.Fatalf("missing script or stylesheet references: %d", len(matches))
		}
		for _, match := range matches {
			asset, err := url.Parse(match[1])
			if err != nil || asset.Query().Get("v") != identity.build+"."+identity.config || previous[asset.Path] == asset.String() {
				t.Fatalf("asset did not follow its page version: %s (%v)", match[1], err)
			}
			previous[asset.Path] = asset.String()
			response := request(t, h, "GET", asset.String(), "")
			if response.Code != 200 || response.Header().Get("Cache-Control") != "no-cache" {
				t.Fatalf("asset %s: %d %v", asset, response.Code, response.Header())
			}
		}
	}
}

func TestRetryResumesFailedRequestWithoutDuplicatingUser(t *testing.T) {
	calls := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			http.Error(w, "bad configuration", 401)
			return
		}
		var body struct {
			Messages []llm.Message `json:"messages"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		users := 0
		for _, m := range body.Messages {
			if m.Role == "user" && m.Content == "hello" {
				users++
			}
		}
		if users != 1 {
			t.Errorf("duplicate user prompt: %+v", body.Messages)
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"OK\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer model.Close()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "test"), SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	dir := t.TempDir()
	h := New(cfg, nil, dir).Routes()
	id := create(t, h)
	first := request(t, h, "POST", "/sessions/"+id+"/messages", `{"text":"hello"}`)
	if strings.Count(first.Body.String(), "event: error\n") != 1 {
		t.Fatal(first.Body.String())
	}
	second := request(t, h, "POST", "/sessions/"+id+"/messages", `{"retry":true}`)
	if strings.Contains(second.Body.String(), "event: error") || !strings.Contains(second.Body.String(), `"text":"OK"`) {
		t.Fatal(second.Body.String())
	}
	saved, err := session.Load(dir, id, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(saved.Messages) != 2 || saved.LastError != "" || saved.Messages[0].Content != "hello" {
		t.Fatalf("bad retry state: %+v", saved)
	}
	if w := request(t, h, "POST", "/sessions/"+id+"/messages", `{"retry":true}`); w.Code != 409 {
		t.Fatal(w.Body.String())
	}
}
