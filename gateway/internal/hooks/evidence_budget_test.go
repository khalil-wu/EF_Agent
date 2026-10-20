package hooks

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"eegagent/gateway/internal/agent"
	"eegagent/gateway/internal/llm"
)

func TestOversizedRoutingRetainsSelectionAndConcreteInputBlockers(t *testing.T) {
	body := marshalForTest(t, map[string]any{
		"status": "ok", "task": "emotion", "manifest_id": "sig_trial", "video_manifest_id": "vid_face",
		"selected_model": "model_ready", "prediction_executed": false,
		"requested_range": map[string]any{"start_s": 66, "end_s": 67, "max_trials": 1},
		"video_alignment": map[string]any{"sync_offset_s": 66, "clock": "video time = original EEG time - sync_offset_s"},
		"candidates": []map[string]any{
			{"model_id": "model_ready", "route_status": "ready", "compatibility": "compatible", "prediction_window_count": 1,
				"input": map[string]any{"trial_s": 63, "baseline_s": 3, "window_s": 1}, "limitations": strings.Repeat("long catalog description", 1000)},
			{"model_id": "model_blocked", "route_status": "incompatible", "compatibility": "incompatible",
				"missing_channels": []string{"O1"}, "reasons": []string{"缺 1 个通道（O1）"}, "missing_context": []string{"需要确认参考"}},
		},
	})
	h := Build(nil, t.TempDir(), nil)
	result, failed := h.AfterTool(context.Background(), &agent.Session{}, agent.ToolCall{Name: "route_model"}, body, false)
	var parsed struct {
		Selected   string                       `json:"selected_model"`
		Candidates []map[string]json.RawMessage `json:"candidates"`
		Range      map[string]json.RawMessage   `json:"requested_range"`
		Alignment  map[string]json.RawMessage   `json:"video_alignment"`
	}
	if err := json.Unmarshal(result, &parsed); err != nil {
		t.Fatal(err)
	}
	if failed || len(result) > maxToolResultChars || parsed.Selected != "model_ready" || len(parsed.Candidates) != 2 {
		t.Fatalf("routing decision lost: %s", result)
	}
	if len(parsed.Candidates[0]["input"]) == 0 || string(parsed.Candidates[0]["prediction_window_count"]) != "1" ||
		string(parsed.Candidates[1]["missing_channels"]) != `["O1"]` || !strings.Contains(string(parsed.Candidates[1]["reasons"]), "O1") ||
		string(parsed.Range["start_s"]) != "66" || string(parsed.Alignment["sync_offset_s"]) != "66" {
		t.Fatalf("input boundaries lost: %s", result)
	}
}

func retrievalChunk(id, text, role string) map[string]any {
	return map[string]any{"chunk_id": id, "text": text, "retrieval_role": role, "page": 7,
		"title": "Methods.pdf", "source": "Methods.pdf · 第 7 页", "source_kind": "paper_excerpt",
		"document_sha256": "original-snapshot", "original_url": "/knowledge/documents/original-snapshot/file"}
}

func retrievalFact(id string, evidence ...string) map[string]any {
	return map[string]any{"fact_id": id, "text": "Method A requires condition B", "evidence": evidence,
		"source": "curated", "status": "accepted"}
}

func marshalForTest(t *testing.T, value any) json.RawMessage {
	t.Helper()
	body, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return body
}

func checkDeliveredEvidence(t *testing.T, body json.RawMessage) Retrieval {
	t.Helper()
	if len(body) > maxToolResultChars {
		t.Fatalf("budget exceeded: %d", len(body))
	}
	var retrieval Retrieval
	if err := json.Unmarshal(body, &retrieval); err != nil {
		t.Fatal(err)
	}
	chunks := map[string]evidenceChunk{}
	for _, raw := range retrieval.Chunks {
		var chunk evidenceChunk
		_ = json.Unmarshal(raw, &chunk)
		chunks[chunk.ID] = chunk
	}
	for _, group := range [][]json.RawMessage{retrieval.Facts, retrieval.Path} {
		for _, raw := range group {
			if bindEvidence(raw, chunks) == nil {
				t.Fatalf("relation lost its readable evidence: %s", raw)
			}
		}
	}
	return retrieval
}

func TestRetrievalBudgetRetainsGraphSupportAfterLowRankMatches(t *testing.T) {
	input := marshalForTest(t, map[string]any{"status": "ok", "query": "Welch requirements", "evidence_scope": "reference_material",
		"entities": []map[string]any{{"id": "method_welch", "name": "Welch"}},
		"chunks": []map[string]any{
			retrievalChunk("k_111111", strings.Repeat("a", 6500), "match"),
			retrievalChunk("k_222222", strings.Repeat("b", 6500), "match"),
			retrievalChunk("k_abcdef", "Overlapping EEG windows are used for the spectral estimate.", "graph_support"),
		}, "facts": []map[string]any{retrievalFact("r_welch", "k_abcdef")}})
	h := Build(nil, t.TempDir(), nil)
	s := &agent.Session{}
	result, failed := h.AfterTool(context.Background(), s, agent.ToolCall{Name: "search_knowledge"}, input, false)
	retrieval := checkDeliveredEvidence(t, result)
	if failed || len(retrieval.Facts) != 1 || len(retrieval.Chunks) != 2 {
		t.Fatalf("supported fact lost: %s", result)
	}
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(result, &fields)
	if string(fields["query"]) != `"Welch requirements"` || string(fields["evidence_scope"]) != `"reference_material"` || len(retrieval.Entities) != 1 {
		t.Fatal("tool metadata lost", string(result))
	}
	if source := s.Sources["k_abcdef"]; !strings.Contains(string(source), "第 7 页") || !strings.Contains(string(source), "original-snapshot") {
		t.Fatal("source metadata lost", string(source))
	}
	if _, absent := s.Sources["k_222222"]; absent {
		t.Fatal("an omitted excerpt was recorded as read")
	}
}

func TestRetrievalBudgetDropsLowestFactWithItsExclusiveSupport(t *testing.T) {
	input := marshalForTest(t, map[string]any{"status": "ok", "chunks": []map[string]any{
		retrievalChunk("k_aaaaaa", strings.Repeat("a", 7000), "graph_support"),
		retrievalChunk("k_bbbbbb", strings.Repeat("b", 7000), "graph_support"),
	}, "facts": []map[string]any{retrievalFact("r_primary", "k_aaaaaa"), retrievalFact("r_secondary", "k_bbbbbb")}})
	result := budgetRetrieval(input, maxToolResultChars)
	retrieval := checkDeliveredEvidence(t, result)
	if len(retrieval.Facts) != 1 || len(retrieval.Chunks) != 1 || !strings.Contains(string(retrieval.Facts[0]), "r_primary") || !strings.Contains(string(retrieval.Chunks[0]), "k_aaaaaa") {
		t.Fatalf("priority or evidence group broken: %s", result)
	}
}

func TestRelationQuoteBindsToTheActualContainingChunk(t *testing.T) {
	fact := retrievalFact("r_quote", "k_111111", "k_abcdef")
	fact["evidence_quote"] = "Alpha estimates require consistent window settings."
	input := marshalForTest(t, map[string]any{"status": "ok", "chunks": []map[string]any{
		retrievalChunk("k_111111", strings.Repeat("unrelated text ", 1000), "match"),
		retrievalChunk("k_abcdef", "Source paragraph: Alpha estimates require\nconsistent window settings.", "graph_support"),
	}, "facts": []map[string]any{fact, retrievalFact("r_missing", "k_999999")}})
	result := budgetRetrieval(input, maxToolResultChars)
	retrieval := checkDeliveredEvidence(t, result)
	if len(retrieval.Facts) != 1 || !strings.Contains(string(retrieval.Facts[0]), `"evidence":["k_abcdef"]`) || !strings.Contains(string(retrieval.Facts[0]), "evidence_quote") {
		t.Fatalf("quote support not preserved: %s", result)
	}
}

func TestKnowledgeGraphPathIsBudgetedAsAWhole(t *testing.T) {
	input := marshalForTest(t, map[string]any{"status": "ok", "from": map[string]any{"id": "a", "name": "A"}, "to": map[string]any{"id": "c", "name": "C"},
		"chunks": []map[string]any{
			retrievalChunk("k_aaaaaa", strings.Repeat("a", 7000), "graph_support"),
			retrievalChunk("k_bbbbbb", strings.Repeat("b", 7000), "graph_support"),
		}, "path": []map[string]any{retrievalFact("r_ab", "k_aaaaaa"), retrievalFact("r_bc", "k_bbbbbb")}})
	h := Build(nil, t.TempDir(), nil)
	s := &agent.Session{}
	result, _ := h.AfterTool(context.Background(), s, agent.ToolCall{Name: "knowledge_graph"}, input, false)
	retrieval := checkDeliveredEvidence(t, result)
	if len(retrieval.Path) != 0 || len(retrieval.Chunks) != 0 || len(s.Sources) != 0 || !strings.Contains(string(result), `"budget_limited":true`) || !strings.Contains(string(result), "不表示图谱中没有路径") {
		t.Fatalf("partial path or incorrect absence claim: %s", result)
	}
	if !strings.Contains(string(result), `"from":{"id":"a","name":"A"}`) || !strings.Contains(string(result), `"to":{"id":"c","name":"C"}`) {
		t.Fatal("path endpoint metadata lost")
	}

	small := marshalForTest(t, map[string]any{"status": "ok", "chunks": []map[string]any{retrievalChunk("k_abcdef", "A is related to B.", "graph_support")}, "path": []map[string]any{retrievalFact("r_ab", "k_abcdef")}})
	result, _ = h.AfterTool(context.Background(), s, agent.ToolCall{Name: "knowledge_graph"}, small, false)
	if retrieval = checkDeliveredEvidence(t, result); len(retrieval.Path) != 1 || len(s.Sources) != 1 {
		t.Fatal("supported graph path sources were not captured")
	}
}

func TestAutomaticRetrievalUsesTheSameEvidenceBudget(t *testing.T) {
	body := marshalForTest(t, map[string]any{"chunks": []map[string]any{
		retrievalChunk("k_111111", strings.Repeat("a", 13000), "match"),
		retrievalChunk("k_abcdef", "Welch uses overlapping EEG windows.", "graph_support"),
	}, "facts": []map[string]any{retrievalFact("r_welch", "k_abcdef")}})
	retrieve := func(context.Context, string, int, string) (*Retrieval, error) {
		var result Retrieval
		_ = json.Unmarshal(body, &result)
		return &result, nil
	}
	h := Build(nil, t.TempDir(), retrieve)
	s := &agent.Session{Messages: []llm.Message{{Role: "user", Content: "Welch 功率谱的原理是什么？"}}}
	system := "SYS"
	if err := h.PrepareRequest(context.Background(), s, &system); err != nil {
		t.Fatal(err)
	}
	bounded := marshalForTest(t, Retrieval{Chunks: s.Knowledge, Facts: s.KnowledgeFacts})
	if retrieval := checkDeliveredEvidence(t, bounded); len(retrieval.Chunks) != 1 || len(retrieval.Facts) != 1 || len(s.Sources) != 1 {
		t.Fatalf("automatic retrieval bypassed budget: %s", bounded)
	}
}

func TestOversizedSingleExcerptReturnsLookupWithoutUnsupportedFact(t *testing.T) {
	body := marshalForTest(t, map[string]any{"status": "ok", "query": "EEG method", "chunks": []map[string]any{
		retrievalChunk("k_abcdef", strings.Repeat("a", 20000), "graph_support"),
	}, "facts": []map[string]any{retrievalFact("r_method", "k_abcdef")}})
	result := budgetRetrieval(body, maxToolResultChars)
	bounded := checkDeliveredEvidence(t, result)
	if len(bounded.Facts) != 0 || len(bounded.Chunks) != 0 || !strings.Contains(string(result), `"omitted_chunk_ids":["k_abcdef"]`) || !strings.Contains(string(result), `"budget_limited":true`) {
		t.Fatalf("oversized excerpt claimed as delivered evidence: %s", result)
	}
}

func TestLargeReadArtifactPreservesNumericalRowsAndScientificContext(t *testing.T) {
	rows := []map[string]any{}
	for i := 40; i < 120; i++ {
		values := make([]float64, 120)
		for j := range values {
			values[j] = float64(i) + float64(j)/1000
		}
		rows = append(rows, map[string]any{"window": i, "start_s": float64(i) * 2, "end_s": float64(i)*2 + 4,
			"original_time_range_s": []float64{100 + float64(i)*2, 104 + float64(i)*2}, "segment_index": 0, "values": values})
	}
	body := marshalForTest(t, map[string]any{"status": "ok", "artifact_id": "art_abcdef", "source_id": "sig_123456", "source_manifest_ids": []string{"sig_123456"}, "parent_artifact_ids": []string{"art_111111"},
		"signal":   map[string]any{"time_axis": "concatenated_epochs", "original_recording_offset_s": 100, "channel_names": []string{"O1", "O2"}},
		"analysis": map[string]any{"welch": map[string]any{"nperseg": 250, "scaling": "density"}}, "axis": "window", "unit": "uV²", "components": []string{"alpha"}, "component_units": []string{"uV²"},
		"part": "bandpower", "channel": "O2", "start": 40, "total": 200, "rows": rows})
	h := Build(nil, t.TempDir(), nil)
	result, failed := h.AfterTool(context.Background(), &agent.Session{}, agent.ToolCall{Name: "read_artifact"}, body, false)
	var parsed struct {
		Rows      []json.RawMessage `json:"rows"`
		Start     int               `json:"start"`
		NextStart int               `json:"next_start"`
		Total     int               `json:"total"`
	}
	if err := json.Unmarshal(result, &parsed); err != nil {
		t.Fatal(err)
	}
	if failed || len(result) > maxToolResultChars || len(parsed.Rows) == 0 || len(parsed.Rows) >= len(rows) || parsed.Start != 40 || parsed.NextStart != 40+len(parsed.Rows) || parsed.Total != 200 {
		t.Fatalf("numeric pagination lost: bytes=%d rows=%d next=%d", len(result), len(parsed.Rows), parsed.NextStart)
	}
	if string(parsed.Rows[0]) != string(marshalForTest(t, rows[0])) {
		t.Fatal("returned numerical row changed")
	}
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(result, &fields)
	for _, key := range []string{"signal", "analysis", "axis", "unit", "components", "component_units", "source_id", "source_manifest_ids", "parent_artifact_ids", "artifact_id", "part", "channel"} {
		var original map[string]json.RawMessage
		_ = json.Unmarshal(body, &original)
		if string(fields[key]) != string(original[key]) {
			t.Fatalf("scientific context changed: %s", key)
		}
	}
}

func TestOversizedEpochCoordinateListsKeepCountsAndCurrentNumericalPage(t *testing.T) {
	segments := []map[string]any{}
	ranges := []map[string]any{}
	for i := 0; i < 1000; i++ {
		segments = append(segments, map[string]any{"start_sample": i * 100, "end_sample": (i + 1) * 100,
			"epoch_index": i, "epoch_tmin_s": -0.2, "original_start_s": float64(i), "original_end_s": float64(i + 1)})
		ranges = append(ranges, map[string]any{"source_time_range_s": []float64{float64(i), float64(i + 1)},
			"epoch_index": i, "epoch_relative_time_range_s": []float64{-0.2, 0.8}})
	}
	row := map[string]any{"window": 432, "segment_index": 432, "epoch_index": 432, "epoch_relative_time_range_s": []float64{-0.2, 0.8}, "values": []float64{19.7}}
	body := marshalForTest(t, map[string]any{"status": "ok", "artifact_id": "art_abcdef", "start": 432, "total": 1000, "rows": []map[string]any{row},
		"signal":   map[string]any{"kind": "epochs", "time_axis": "packed epochs; boundaries are discontinuous", "sample_rate_hz": 100, "channel_names": []string{"O2"}, "segments": segments},
		"analysis": map[string]any{"source_id": "sig_abcdef", "time_axis": "seconds from current signal sample 0; packed epochs are discontinuous", "original_time_ranges_s": ranges}})
	result := budgetToolResult(body, maxToolResultChars)
	var parsed struct {
		Signal   map[string]json.RawMessage `json:"signal"`
		Analysis map[string]json.RawMessage `json:"analysis"`
		Rows     []json.RawMessage          `json:"rows"`
	}
	if err := json.Unmarshal(result, &parsed); err != nil {
		t.Fatal(err)
	}
	if len(result) > maxToolResultChars || len(parsed.Rows) != 1 || string(parsed.Rows[0]) != string(marshalForTest(t, row)) {
		t.Fatalf("epoch page lost to metadata: %d %s", len(result), parsed.Rows)
	}
	if string(parsed.Signal["n_segments"]) != "1000" || string(parsed.Signal["segments_omitted"]) != "true" || len(parsed.Signal["segments"]) != 0 || len(parsed.Signal["time_axis"]) == 0 || string(parsed.Signal["kind"]) != `"epochs"` {
		t.Fatal("epoch axis or segment scope lost", parsed.Signal)
	}
	if string(parsed.Analysis["n_time_ranges"]) != "1000" || string(parsed.Analysis["time_ranges_omitted"]) != "true" || len(parsed.Analysis["original_time_ranges_s"]) != 0 || len(parsed.Analysis["original_time_ranges_s_preview"]) == 0 {
		t.Fatal("analysis range scope lost", parsed.Analysis)
	}
}

func TestNestedFullArtifactRetainsScientificMetadataUnderBudget(t *testing.T) {
	segments := []map[string]any{}
	ranges := []map[string]any{}
	for i := 0; i < 1000; i++ {
		segments = append(segments, map[string]any{"start_sample": i * 250, "end_sample": (i + 1) * 250, "epoch_index": i, "epoch_tmin_s": -0.2})
		ranges = append(ranges, map[string]any{"source_time_range_s": []float64{float64(i), float64(i + 1)}, "epoch_index": i})
	}
	record := map[string]any{"artifact_id": "art_abcdef", "kind": "features", "source_manifest_ids": []string{"sig_123456"}, "parent_artifact_ids": []string{"art_111111"},
		"shape": []int{1000, 1, 5}, "time_range_s": []float64{0, 1000},
		"signal":     map[string]any{"kind": "epochs", "sample_rate_hz": 250, "amplitude_unit": "uV", "channel_names": []string{"O2"}, "segments": segments},
		"analysis":   map[string]any{"source_id": "sig_123456", "time_axis": "packed epochs; boundaries are discontinuous", "original_time_ranges_s": ranges},
		"summary":    map[string]any{"channel_names": []string{"O2"}, "power_unit": "uV²"},
		"provenance": map[string]any{"created_by": "extract_features@1.5", "steps": []string{"segment_1_1"}}}
	body := marshalForTest(t, map[string]any{"status": "ok", "artifact": record, "part": "summary"})
	h := Build(nil, t.TempDir(), nil)
	result, failed := h.AfterTool(context.Background(), &agent.Session{}, agent.ToolCall{Name: "read_artifact"}, body, false)
	var parsed struct {
		Artifact struct {
			ID       string                     `json:"artifact_id"`
			Signal   map[string]json.RawMessage `json:"signal"`
			Analysis map[string]json.RawMessage `json:"analysis"`
			Sources  []string                   `json:"source_manifest_ids"`
			Parents  []string                   `json:"parent_artifact_ids"`
		} `json:"artifact"`
	}
	if err := json.Unmarshal(result, &parsed); err != nil {
		t.Fatal(err)
	}
	if failed || len(result) > maxToolResultChars || parsed.Artifact.ID != "art_abcdef" || len(parsed.Artifact.Sources) != 1 || len(parsed.Artifact.Parents) != 1 {
		t.Fatalf("nested scientific artifact lost: %d %s", len(result), result)
	}
	if string(parsed.Artifact.Signal["n_segments"]) != "1000" || len(parsed.Artifact.Signal["segments"]) != 0 || string(parsed.Artifact.Signal["amplitude_unit"]) != `"uV"` || string(parsed.Artifact.Signal["kind"]) != `"epochs"` {
		t.Fatal("nested epoch geometry became incomplete or oversized", parsed.Artifact.Signal)
	}
	if len(parsed.Artifact.Analysis["time_axis"]) == 0 || string(parsed.Artifact.Analysis["n_time_ranges"]) != "1000" || len(parsed.Artifact.Analysis["original_time_ranges_s"]) != 0 {
		t.Fatal("nested analysis coordinates lost", parsed.Artifact.Analysis)
	}
}

func TestDescriptiveFieldsDoNotDisplaceAllNumericalRows(t *testing.T) {
	rows := []map[string]any{}
	for i := 0; i < 40; i++ {
		rows = append(rows, map[string]any{"window": i, "values": []float64{23.8, 0.7, 1.2}})
	}
	body := marshalForTest(t, map[string]any{"status": "ok", "artifact_id": "art_abcdef", "start": 0, "total": 40,
		"signal": map[string]any{"kind": "continuous", "time_offset_s": 300, "channel_names": []string{"O2"}}, "unit": "uV²", "components": []string{"alpha", "beta", "gamma"},
		"stdout": strings.Repeat("s", 5900), "summary": map[string]any{"description": strings.Repeat("d", 5900)}, "rows": rows})
	result := budgetToolResult(body, maxToolResultChars)
	var parsed struct {
		Rows []json.RawMessage `json:"rows"`
	}
	if err := json.Unmarshal(result, &parsed); err != nil {
		t.Fatal(err)
	}
	if len(result) > maxToolResultChars || len(parsed.Rows) == 0 || string(parsed.Rows[0]) != string(marshalForTest(t, rows[0])) {
		t.Fatalf("descriptions displaced numerical evidence: %d rows=%d", len(result), len(parsed.Rows))
	}
}

func TestLargeScriptResultKeepsWorkspaceDestinationsAndDiagnosticPaths(t *testing.T) {
	files := []map[string]string{}
	for i := 0; i < 200; i++ {
		files = append(files, map[string]string{"workspace_id": "ws_abcdef123456", "path": fmt.Sprintf("results/subject-%03d.csv", i), "name": fmt.Sprintf("subject-%03d.csv", i), "kind": "file"})
	}
	body := marshalForTest(t, map[string]any{"status": "ok", "code_path": "/storage/scratch/session/run_001.py", "full_output_path": "/storage/scratch/session/run_001.out", "run_id": "run_001", "workspace_files": files, "stdout": strings.Repeat("batch log\n", 3000)})
	result := budgetToolResult(body, maxToolResultChars)
	var parsed struct {
		CodePath   string              `json:"code_path"`
		OutputPath string              `json:"full_output_path"`
		Files      []map[string]string `json:"workspace_files"`
		Count      int                 `json:"workspace_files_count"`
		Omitted    bool                `json:"workspace_files_omitted"`
	}
	if err := json.Unmarshal(result, &parsed); err != nil {
		t.Fatal(err)
	}
	if len(result) > maxToolResultChars || parsed.Count != len(files) || !parsed.Omitted || len(parsed.Files) == 0 || len(parsed.Files) >= parsed.Count || parsed.CodePath == "" || parsed.OutputPath == "" {
		t.Fatalf("script destinations or diagnostic paths were lost: %s", result)
	}
	if parsed.Files[0]["path"] != files[0]["path"] || parsed.Files[0]["workspace_id"] != files[0]["workspace_id"] {
		t.Fatalf("saved file reference changed: %+v", parsed.Files[0])
	}
}
