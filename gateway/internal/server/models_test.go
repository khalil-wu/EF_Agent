package server

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/worker"
)

func TestModelCatalogProxiesWorkerStatusAndBody(t *testing.T) {
	for _, code := range []int{http.StatusOK, http.StatusServiceUnavailable} {
		upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/models" {
				t.Errorf("unexpected worker path %s", r.URL.Path)
			}
			w.WriteHeader(code)
			io.WriteString(w, `{"models":[{"model_id":"camer_deap_v1","inference_available":false}]}`)
		}))
		s := New(agent.Config{}, worker.New(upstream.URL), t.TempDir())
		response := httptest.NewRecorder()
		s.Routes().ServeHTTP(response, httptest.NewRequest("GET", "/models", nil))
		upstream.Close()
		if response.Code != code || !strings.Contains(response.Body.String(), `"inference_available":false`) {
			t.Fatalf("catalog response: %d %s", response.Code, response.Body.String())
		}
	}
}
