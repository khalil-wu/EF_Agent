package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestCancelledExecutionStopsWorkerProcess(t *testing.T) {
	for _, name := range []string{"run_python", "run_model"} {
		t.Run(name, func(t *testing.T) {
			started := make(chan struct{})
			stopped := make(chan struct{}, 1)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/tools/" + name:
					io.Copy(io.Discard, r.Body)
					close(started)
					<-r.Context().Done()
				case "/sessions/ses_abcdef/stop":
					stopped <- struct{}{}
					w.Write([]byte(`{"status":"stopped"}`))
				default:
					t.Errorf("unexpected request %s", r.URL.Path)
					w.WriteHeader(404)
				}
			}))
			defer server.Close()
			client := New(server.URL)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan error, 1)
			go func() {
				_, err := client.Call(ctx, name, json.RawMessage(`{"_session_id":"ses_abcdef","code":"import time; time.sleep(20)"}`))
				done <- err
			}()
			<-started
			cancel()
			select {
			case <-stopped:
			case <-time.After(3 * time.Second):
				t.Fatal("worker stop request was not sent")
			}
			if err := <-done; !errors.Is(err, context.Canceled) {
				t.Fatal(err)
			}
		})
	}
}

func TestBusinessFailureIsAnErrorEvenWithHTTP200(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte(`{"status":"error","error":{"code":"Timeout","message":"execution timed out"}}`))
	}))
	defer backend.Close()
	body, err := New(backend.URL).Call(context.Background(), "run_python", json.RawMessage(`{}`))
	if err == nil || !json.Valid(body) {
		t.Fatalf("lost business failure: %s %v", body, err)
	}
	var result struct{ Status string }
	json.Unmarshal(body, &result)
	if result.Status != "error" {
		t.Fatal(string(body))
	}
}

func TestExecutionStagesArriveBeforeResult(t *testing.T) {
	first := make(chan struct{})
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/tools/run_model/events" {
			t.Errorf("path %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte("{\"type\":\"progress\",\"progress\":{\"stage\":\"upload_eeg\",\"label\":\"上传 EEG 输入\"}}\n"))
		w.(http.Flusher).Flush()
		<-first
		w.Write([]byte("{\"type\":\"result\",\"result\":{\"status\":\"error\",\"error\":{\"code\":\"Remote\",\"message\":\"inference failed\"}}}\n"))
	}))
	defer backend.Close()
	var stage string
	body, err := New(backend.URL).CallWithProgress(context.Background(), "run_model", json.RawMessage(`{}`), func(value json.RawMessage) {
		var item struct{ Stage string }
		json.Unmarshal(value, &item)
		stage = item.Stage
		close(first)
	})
	if stage != "upload_eeg" || err == nil || !json.Valid(body) {
		t.Fatalf("stage=%s result=%s error=%v", stage, body, err)
	}
}

func TestCancellationAfterProgressStopsRemoteExecution(t *testing.T) {
	stopped := make(chan struct{}, 1)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/sessions/stream-fixture/stop" {
			stopped <- struct{}{}
			w.Write([]byte(`{"status":"stopped"}`))
			return
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte("{\"type\":\"progress\",\"progress\":{\"stage\":\"inference\"}}\n"))
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer backend.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	_, err := New(backend.URL).CallWithProgress(ctx, "run_model", json.RawMessage(`{"_session_id":"stream-fixture"}`), func(json.RawMessage) { cancel() })
	if !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("streamed remote job not stopped")
	}
}

func TestCancelledScriptRecoversCompletedOutputsFromItsStopResponse(t *testing.T) {
	for _, name := range []string{"run_python", "run_model"} {
		t.Run(name, func(t *testing.T) {
			partial := json.RawMessage(`{"status":"error","error":{"code":"Cancelled","message":"用户已停止脚本"},"artifacts":[{"artifact_id":"art_abcdef123456","kind":"table","uri":"first.csv","summary":{"rows":1}}],"workspace_files":[{"workspace_id":"ws_abcdef123456","path":"results/first.csv","name":"first.csv","kind":"file"}]}`)
			stopped := make(chan struct{}, 1)
			backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/sessions/ses_partial/stop":
					stopped <- struct{}{}
					json.NewEncoder(w).Encode(map[string]any{"status": "stopped", "result": partial})
				case "/tools/" + name + "/events":
					io.Copy(io.Discard, r.Body)
					w.Header().Set("Content-Type", "application/x-ndjson")
					w.Write([]byte("{\"type\":\"progress\",\"progress\":{\"stage\":\"first_file_complete\"}}\n"))
					w.(http.Flusher).Flush()
					<-r.Context().Done()
				default:
					t.Errorf("unexpected request: %s", r.URL.Path)
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer backend.Close()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			body, err := New(backend.URL).CallWithProgress(ctx, name, json.RawMessage(`{"_session_id":"ses_partial"}`), func(json.RawMessage) { cancel() })
			if !errors.Is(err, context.Canceled) {
				t.Fatalf("cancellation was lost: %s %v", body, err)
			}
			select {
			case <-stopped:
			default:
				t.Fatal("the current job was not stopped")
			}
			if name == "run_python" && string(body) != string(partial) {
				t.Fatalf("completed script output was dropped after transport cancellation: %s", body)
			}
			if name != "run_python" && len(body) != 0 {
				t.Fatalf("another tool adopted a script result: %s", body)
			}
		})
	}
}
