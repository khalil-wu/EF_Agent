package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
	"eegagent/gateway/internal/worker"
)

type uploadProbeWriter struct {
	http.ResponseWriter
	handler   http.Handler
	sessionID string
	status    int
	probed    bool
}

func (w *uploadProbeWriter) Write(b []byte) (int, error) {
	if !w.probed {
		w.probed = true
		var result struct {
			Manifest struct {
				ID string `json:"manifest_id"`
			} `json:"manifest"`
		}
		if err := json.Unmarshal(b, &result); err == nil && result.Manifest.ID != "" {
			rec := httptest.NewRecorder()
			w.handler.ServeHTTP(rec, httptest.NewRequest("GET", "/sessions/"+w.sessionID+"/attachments/"+result.Manifest.ID+"/file?view=1", nil))
			w.status = rec.Code
		}
	}
	return w.ResponseWriter.Write(b)
}

// The thumbnail request fires as soon as the upload response arrives; the snapshot must be
// published before the 200 is written or it 404s and the chip stays broken.
func TestUploadPublishesSnapshotBeforeResponseIsWritten(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Args struct {
				Path string `json:"path"`
			} `json:"args"`
		}
		_ = json.NewDecoder(r.Body).Decode(&input)
		name := filepath.Base(input.Args.Path)
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "manifest": map[string]any{
			"manifest_id": "img_" + strings.TrimSuffix(name, ".png"), "modality": "image", "attachment_sha256": name,
			"source_path": input.Args.Path, "vision_path": input.Args.Path, "source_format": "png", "media_type": "image/png", "width": 16, "height": 12}})
	}))
	defer backend.Close()
	cfg := agent.Config{LLM: llm.New("http://127.0.0.1:9", "", "fixture"), SystemFn: func(*agent.Session) string { return "system" }}
	s := New(cfg, worker.New(backend.URL), t.TempDir())
	h := s.Routes()
	id := create(t, h)

	var picture bytes.Buffer
	raster := image.NewRGBA(image.Rect(0, 0, 16, 12))
	raster.Set(3, 4, color.RGBA{R: 255, A: 255})
	if err := png.Encode(&picture, raster); err != nil {
		t.Fatal(err)
	}
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	file, _ := form.CreateFormFile("file", "photo.png")
	_, _ = file.Write(picture.Bytes())
	_ = form.WriteField("staged", "true")
	_ = form.Close()
	r := httptest.NewRequest("POST", "/sessions/"+id+"/attachments", &body)
	r.Header.Set("Content-Type", form.FormDataContentType())
	w := &uploadProbeWriter{ResponseWriter: httptest.NewRecorder(), handler: h, sessionID: id}
	h.ServeHTTP(w, r)
	if w.status != 200 {
		t.Fatalf("GET attachment file inside the response write got %d, want 200", w.status)
	}
	fmt.Println("probe status:", w.status)
}

// The worker records paths with symlinks resolved (e.g. /tmp → /private/tmp on macOS);
// containment must compare canonical paths or every served attachment 404s.
func TestAttachmentFileToleratesSymlinkResolvedManifestPaths(t *testing.T) {
	dir := t.TempDir()
	upload := filepath.Join(dir, "uploads", "ses_1abc23", "upl_1", "f.png")
	if err := os.MkdirAll(filepath.Dir(upload), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(upload, []byte("png"), 0o644); err != nil {
		t.Fatal(err)
	}
	resolvedUpload, _ := filepath.EvalSymlinks(upload)
	record := `{"session_id":"ses_1abc23","title":"t","staged_manifests":[{"manifest_id":"img_1abc23","modality":"image","source_path":` +
		fmt.Sprintf("%q", resolvedUpload) + `,"vision_path":` + fmt.Sprintf("%q", resolvedUpload) +
		`,"media_type":"image/png","original_name":"f.png"}]}`
	if err := os.MkdirAll(filepath.Join(dir, "sessions"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "sessions", "ses_1abc23.json"), []byte(record), 0o644); err != nil {
		t.Fatal(err)
	}
	h := New(agent.Config{}, nil, dir).Routes()
	w := request(t, h, "GET", "/sessions/ses_1abc23/attachments/img_1abc23/file?view=1", "")
	if w.Code != 200 || w.Body.String() != "png" {
		t.Fatalf("attachment file under resolved path: %d %s", w.Code, w.Body.String())
	}
}
