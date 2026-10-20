package hooks

import (
	"encoding/json"
	"strings"

	"eegagent/gateway/internal/artifacts"
)

type evidenceChunk struct {
	ID   string `json:"chunk_id"`
	Text string `json:"text"`
	Role string `json:"retrieval_role"`
}

// Bind each relation to one delivered source. A quoted relation needs the chunk containing its quote.
func bindEvidence(raw json.RawMessage, chunks map[string]evidenceChunk) json.RawMessage {
	var fact struct {
		Evidence []string `json:"evidence"`
		Quote    string   `json:"evidence_quote"`
	}
	_ = json.Unmarshal(raw, &fact)
	quote := strings.ToLower(strings.Join(strings.Fields(fact.Quote), " "))
	for _, id := range fact.Evidence {
		chunk, exists := chunks[id]
		text := strings.ToLower(strings.Join(strings.Fields(chunk.Text), " "))
		if !exists || text == "" || (quote != "" && !strings.Contains(text, quote)) {
			continue
		}
		var fields map[string]json.RawMessage
		_ = json.Unmarshal(raw, &fields)
		fields["evidence"], _ = json.Marshal([]string{id})
		bound, _ := json.Marshal(fields)
		return bound
	}
	return nil
}

func evidenceDependencies(facts, path []json.RawMessage) map[string]bool {
	used := map[string]bool{}
	for _, relations := range [][]json.RawMessage{facts, path} {
		for _, raw := range relations {
			var fact struct {
				Evidence []string `json:"evidence"`
			}
			_ = json.Unmarshal(raw, &fact)
			for _, id := range fact.Evidence {
				used[id] = true
			}
		}
	}
	return used
}

// budgetRetrieval removes complete evidence groups, retaining every kept source's original metadata and text.
func budgetRetrieval(result json.RawMessage, limit int) json.RawMessage {
	var fields map[string]json.RawMessage
	if json.Unmarshal(result, &fields) != nil {
		return result
	}
	var retrieved Retrieval
	_ = json.Unmarshal(result, &retrieved)
	chunks := map[string]evidenceChunk{}
	for _, raw := range retrieved.Chunks {
		var chunk evidenceChunk
		_ = json.Unmarshal(raw, &chunk)
		chunks[chunk.ID] = chunk
	}
	facts := make([]json.RawMessage, 0, len(retrieved.Facts))
	for _, raw := range retrieved.Facts {
		if bound := bindEvidence(raw, chunks); bound != nil {
			facts = append(facts, bound)
		}
	}
	path := make([]json.RawMessage, 0, len(retrieved.Path))
	for _, raw := range retrieved.Path {
		if bound := bindEvidence(raw, chunks); bound != nil {
			path = append(path, bound)
		}
	}
	if len(path) != len(retrieved.Path) {
		path = []json.RawMessage{}
	}
	unsupported := len(facts) != len(retrieved.Facts) || len(path) != len(retrieved.Path)
	limited, pathLimited := false, false
	var omitted []string
	var originalNote string
	_ = json.Unmarshal(fields["note"], &originalNote)
	encode := func() json.RawMessage {
		fields["chunks"], _ = json.Marshal(retrieved.Chunks)
		if _, present := fields["facts"]; present {
			fields["facts"], _ = json.Marshal(facts)
		}
		if _, present := fields["path"]; present {
			fields["path"], _ = json.Marshal(path)
		}
		if limited || unsupported {
			note := originalNote
			if limited {
				fields["budget_limited"] = json.RawMessage("true")
				note += " 结果超过上下文预算，已省略部分完整片段或关系；未提供正文的片段仅作定位。"
			}
			if pathLimited {
				note += " 路径整体超过预算，已省略；这不表示图谱中没有路径。"
			}
			if unsupported {
				fields["evidence_limited"] = json.RawMessage("true")
				note += " 未提供可读支持的关系已省略。"
			}
			fields["note"], _ = json.Marshal(strings.TrimSpace(note))
			if len(omitted) > 0 {
				fields["omitted_chunk_ids"], _ = json.Marshal(omitted)
			}
		}
		body, _ := json.Marshal(fields)
		return body
	}
	removeChunk := func(i int) {
		var chunk evidenceChunk
		_ = json.Unmarshal(retrieved.Chunks[i], &chunk)
		omitted = append(omitted, chunk.ID)
		retrieved.Chunks = append(retrieved.Chunks[:i:i], retrieved.Chunks[i+1:]...)
	}
	body := encode()
	for len(body) > limit {
		limited = true
		used := evidenceDependencies(facts, path)
		removed := false
		for i := len(retrieved.Chunks) - 1; i >= 0; i-- {
			var chunk evidenceChunk
			_ = json.Unmarshal(retrieved.Chunks[i], &chunk)
			if !used[chunk.ID] {
				removeChunk(i)
				removed = true
				break
			}
		}
		if !removed {
			switch {
			case len(facts) > 0:
				facts = facts[:len(facts)-1]
			case len(path) > 0:
				path = []json.RawMessage{}
				pathLimited = true
			case len(retrieved.Entities) > 0:
				retrieved.Entities = retrieved.Entities[:len(retrieved.Entities)-1]
				fields["entities"], _ = json.Marshal(retrieved.Entities)
			default:
				return body
			}
			remaining := evidenceDependencies(facts, path)
			for i := len(retrieved.Chunks) - 1; i >= 0; i-- {
				var chunk evidenceChunk
				_ = json.Unmarshal(retrieved.Chunks[i], &chunk)
				if chunk.Role == "graph_support" && used[chunk.ID] && !remaining[chunk.ID] {
					removeChunk(i)
				}
			}
		}
		body = encode()
	}
	return body
}

// Project only the named scientific coordinate lists; current-page coordinates stay in the numerical rows.
func projectCoordinateLists(fields map[string]json.RawMessage) {
	for _, spec := range []struct{ context, list, count, omitted, preview string }{
		{"signal", "segments", "n_segments", "segments_omitted", "segments_preview"},
		{"analysis", "original_time_ranges_s", "n_time_ranges", "time_ranges_omitted", "original_time_ranges_s_preview"},
		{"analysis", "window_support", "n_window_support", "window_support_omitted", "window_support_preview"},
	} {
		var context map[string]json.RawMessage
		_ = json.Unmarshal(fields[spec.context], &context)
		var list []json.RawMessage
		_ = json.Unmarshal(context[spec.list], &list)
		if len(list) <= 1 {
			continue
		}
		context[spec.count], _ = json.Marshal(len(list))
		context[spec.omitted] = json.RawMessage("true")
		context[spec.preview], _ = json.Marshal([]json.RawMessage{list[0], list[len(list)-1]})
		delete(context, spec.list)
		context["coordinate_lookup_note"], _ = json.Marshal("完整坐标在源产物；segments 用 read_artifact(part=segments) 分页，窗口坐标随当前数值行返回。")
		fields[spec.context], _ = json.Marshal(context)
	}
}

// Large scientific results retain their coordinates and a pageable prefix of actual numerical rows.
func budgetToolResult(result json.RawMessage, limit int) json.RawMessage {
	var full map[string]json.RawMessage
	if json.Unmarshal(result, &full) != nil {
		return result
	}
	keep := map[string]json.RawMessage{"budget_limited": json.RawMessage("true")}
	for _, key := range []string{"status", "artifact_id", "source_id", "source_manifest_ids", "parent_artifact_ids", "parents", "source", "signal", "analysis", "axis", "unit", "components", "component_units", "part", "channel", "aggregation", "start", "total", "manifest", "error", "code_path", "full_output_path", "run_id", "elapsed_s", "files", "workspace_file", "task", "manifest_id", "video_manifest_id", "selected_model", "video_alignment", "sync_offset_s", "requested_range", "prediction_executed", "analysis_available", "result_links"} {
		if value, exists := full[key]; exists {
			keep[key] = value
		}
	}
	if raw, exists := full["workspace_files"]; exists {
		var files []json.RawMessage
		_ = json.Unmarshal(raw, &files)
		keep["workspace_files_count"], _ = json.Marshal(len(files))
		if len(files) > 8 {
			files = files[:8]
			keep["workspace_files_omitted"] = json.RawMessage("true")
		}
		keep["workspace_files"], _ = json.Marshal(files)
	}
	// Routing decisions must survive omission of long model catalog descriptions.
	if raw, exists := full["candidates"]; exists {
		var candidates []map[string]json.RawMessage
		_ = json.Unmarshal(raw, &candidates)
		for i, candidate := range candidates {
			compact := map[string]json.RawMessage{}
			for _, key := range []string{"model_id", "route_status", "compatibility", "missing_context", "missing_channels", "reasons", "inference_available", "prediction_window_count"} {
				if value, exists := candidate[key]; exists {
					compact[key] = value
				}
			}
			if string(candidate["model_id"]) == string(full["selected_model"]) {
				compact["input"] = candidate["input"]
			}
			candidates[i] = compact
		}
		keep["candidates"], _ = json.Marshal(candidates)
	}
	if _, exists := keep["status"]; !exists {
		keep["status"] = json.RawMessage(`"ok_truncated"`)
	}
	if raw, exists := full["artifact"]; exists {
		keep["artifact"] = artifacts.Compact(raw)
	}
	if raw, exists := full["artifacts"]; exists {
		var list []json.RawMessage
		_ = json.Unmarshal(raw, &list)
		for i, record := range list {
			list[i] = artifacts.Compact(record)
		}
		keep["artifacts"], _ = json.Marshal(list)
	}
	for _, key := range []string{"summary", "stdout"} {
		if value, exists := full[key]; exists && len(value) < 6000 {
			keep[key] = value
		}
	}
	var rows []json.RawMessage
	_ = json.Unmarshal(full["rows"], &rows)
	start := 0
	_ = json.Unmarshal(full["start"], &start)
	_, paged := full["total"]
	encode := func() json.RawMessage {
		note := "结果超过上下文预算；已保留科学参数，完整结果可继续分页读取。"
		if _, exists := full["rows"]; exists {
			keep["rows"], _ = json.Marshal(rows)
			if paged {
				keep["next_start"], _ = json.Marshal(start + len(rows))
				note = "结果超过上下文预算；返回完整数值行的前缀，按 next_start 继续读取，total 为总行数。"
			}
		}
		if _, exists := keep["workspace_files_omitted"]; exists {
			note += "workspace_files 为实际保存路径预览，workspace_files_count 是完整数量；按返回目录查找其余文件，脚本及完整输出见 code_path/full_output_path。"
		}
		keep["note"], _ = json.Marshal(note)
		body, _ := json.Marshal(keep)
		return body
	}
	oneRowBody := func() json.RawMessage {
		allRows := rows
		if len(rows) > 1 {
			rows = rows[:1]
		}
		body := encode()
		rows = allRows
		return body
	}
	minimum := oneRowBody()
	if len(minimum) > limit {
		delete(keep, "stdout")
		minimum = oneRowBody()
	}
	// Full coordinate lists can occur in a direct numerical page or in nested artifact records.
	if len(minimum) > limit {
		projectCoordinateLists(keep)
		if raw, exists := keep["artifact"]; exists {
			var record map[string]json.RawMessage
			_ = json.Unmarshal(raw, &record)
			projectCoordinateLists(record)
			keep["artifact"], _ = json.Marshal(record)
		}
		if raw, exists := keep["artifacts"]; exists {
			var records []map[string]json.RawMessage
			_ = json.Unmarshal(raw, &records)
			for _, record := range records {
				projectCoordinateLists(record)
			}
			keep["artifacts"], _ = json.Marshal(records)
		}
		minimum = oneRowBody()
	}
	if len(minimum) > limit {
		// Drop a duplicate descriptive summary before removing the last usable numerical row.
		delete(keep, "summary")
	}
	body := encode()
	for len(body) > limit && len(rows) > 0 {
		rows = rows[:len(rows)-1]
		body = encode()
	}
	return body
}
