package server

import (
	"bytes"
	"encoding/base64"
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
	"eegagent/gateway/internal/session"
	"eegagent/gateway/internal/worker"
)

func TestUploadedImagePixelsReachTheProviderOnlyAfterMessageSubmission(t *testing.T) {
	var picture bytes.Buffer
	raster := image.NewRGBA(image.Rect(0, 0, 16, 12))
	raster.Set(3, 4, color.RGBA{R: 255, A: 255})
	if err := png.Encode(&picture, raster); err != nil {
		t.Fatal(err)
	}
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Args struct {
				Path string `json:"path"`
			} `json:"args"`
		}
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
		}
		name := filepath.Base(input.Args.Path)
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "manifest": map[string]any{
			"manifest_id": "img_" + strings.TrimSuffix(name, ".png"), "modality": "image", "attachment_sha256": name,
			"source_path": input.Args.Path, "vision_path": input.Args.Path, "source_format": "png", "media_type": "image/png", "width": 16, "height": 12}})
	}))
	defer backend.Close()
	modelCalls := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload struct {
			Messages []struct {
				Role    string          `json:"role"`
				Content json.RawMessage `json:"content"`
			} `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		images := 0
		for _, message := range payload.Messages {
			if len(message.Content) == 0 || message.Content[0] != '[' {
				continue
			}
			var parts []struct {
				Type     string `json:"type"`
				Text     string `json:"text"`
				ImageURL struct {
					URL string `json:"url"`
				} `json:"image_url"`
			}
			if err := json.Unmarshal(message.Content, &parts); err != nil {
				t.Error(err)
			}
			for _, part := range parts {
				if strings.Contains(part.Text, "unsubmitted") {
					t.Error("draft image leaked into provider context")
				}
				if part.Type != "image_url" {
					continue
				}
				images++
				if !strings.HasPrefix(part.ImageURL.URL, "data:image/png;base64,") {
					t.Error("provider did not receive an inline PNG image")
				}
				pixels, err := base64.StdEncoding.DecodeString(strings.TrimPrefix(part.ImageURL.URL, "data:image/png;base64,"))
				if err != nil || !bytes.Equal(pixels, picture.Bytes()) {
					t.Error("provider payload does not contain the actual uploaded pixels")
				}
			}
		}
		if images != 1 {
			t.Errorf("expected one selected picture, received %d", images)
		}
		modelCalls++
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"图中显示所选图片。\"}}]}\n\ndata: [DONE]\n\n")
	}))
	defer model.Close()
	cfg := agent.Config{LLM: llm.New(model.URL, "", "vision-fixture"), SystemFn: func(*agent.Session) string { return "system" }, FileCtxFn: func(*agent.Session) string { return "context" }}
	storage := t.TempDir()
	s := New(cfg, worker.New(backend.URL), storage)
	handler := s.Routes()
	id := create(t, handler)
	upload := func(name string) string {
		var body bytes.Buffer
		form := multipart.NewWriter(&body)
		part, _ := form.CreateFormFile("file", name)
		_, _ = part.Write(picture.Bytes())
		_ = form.WriteField("staged", "true")
		_ = form.Close()
		r := httptest.NewRequest("POST", "/sessions/"+id+"/attachments", &body)
		r.Header.Set("Content-Type", form.FormDataContentType())
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
		var result struct {
			Manifest struct {
				ID string `json:"manifest_id"`
			} `json:"manifest"`
		}
		_ = json.Unmarshal(w.Body.Bytes(), &result)
		return result.Manifest.ID
	}
	selected := upload("selected.png")
	_ = upload("unsubmitted.png")
	if len(s.sessions[id].Manifests) != 0 || modelCalls != 0 {
		t.Fatal("image entered analysis before message submission")
	}
	preview := request(t, handler, "GET", "/sessions/"+id+"/attachments/"+selected+"/preview", "")
	if preview.Code != 200 || !strings.Contains(preview.Body.String(), `"modality":"image"`) {
		t.Fatal("uploaded image has no real attachment preview", preview.Code, preview.Body.String())
	}
	var previewBody struct {
		FileURL string `json:"file_url"`
	}
	_ = json.Unmarshal(preview.Body.Bytes(), &previewBody)
	view := request(t, handler, "GET", previewBody.FileURL, "")
	if view.Code != 200 || !bytes.Equal(view.Body.Bytes(), picture.Bytes()) || view.Header().Get("Content-Type") != "image/png" {
		t.Fatal("image preview did not serve the real display raster", view.Code)
	}
	input, _ := json.Marshal(map[string]any{"attachments": []string{selected}})
	response := request(t, handler, "POST", "/sessions/"+id+"/messages", string(input))
	if response.Code != 200 || !strings.Contains(response.Body.String(), "图中显示") || modelCalls != 1 {
		t.Fatal("image-only question failed", response.Code, response.Body.String(), modelCalls)
	}
	saved, err := session.Load(storage, id, nil)
	if err != nil || saved.Manifests[0].VisionPath == "" || saved.Messages[0].Presentation.Attachments[0].Modality != "image" {
		t.Fatal("image identity did not survive reload", err)
	}
	serialized, _ := os.ReadFile(filepath.Join(storage, "sessions", id+".json"))
	if bytes.Contains(serialized, []byte("data:image")) {
		t.Fatal("base64 pixels bloated the saved text transcript")
	}
}
