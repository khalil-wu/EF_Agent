package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"eegagent/gateway/internal/artifacts"
	"eegagent/gateway/internal/contracts"
	"eegagent/gateway/internal/llm"
)

const visionObservationSystem = `你是 EFAgent 科学视觉识图模块，负责客观读取图像并提炼结构化视觉事实供主推理模型分析。
请严格遵循：
1. 准确读取图中的标题、横纵坐标轴名称、刻度范围与物理单位。
2. 准确识别图例、通道名称、颜色/线型对应关系与标注文字。
3. 客观描述可见趋势、主峰/局部极值位置、突变/伪迹时段或界面异常区域。
4. 仅陈述图像像素中明确可见的内容；不编造图中未标注的数值、统计量或因果结论。`

const inspectImageSchema = `{"type":"object","properties":{"target_id":{"type":"string","description":"图片 manifest_id（img_…）、图表 artifact_id（art_…）或当前工作区图片相对路径"},"question":{"type":"string","description":"需要重点观察和核对的具体视觉问题"}},"required":["target_id"]}`

func inspectImageSpec() llm.ToolSpec {
	return llm.NewToolSpec(
		"inspect_image",
		"使用识图模型查看本会话已上传图片（img_…）、已生成的分析图表产物（art_…，如波形图、功率谱、ICA 头皮图、ASR 对照图）或当前工作区中的图片文件，核对坐标轴、图例、谱峰、波形伪迹或文字细节。",
		json.RawMessage(inspectImageSchema),
	)
}

func isCrossModelVision(cfg Config) bool {
	return cfg.VisionLLM != nil && cfg.VisionLLM.Model != "" && cfg.LLM != nil && cfg.VisionLLM.Model != cfg.LLM.Model
}

func isImageFileExt(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff":
		return true
	default:
		return false
	}
}

func mediaTypeForExt(path string) string {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".webp":
		return "image/webp"
	case ".gif":
		return "image/gif"
	default:
		return "image/png"
	}
}

func (s *Session) ensureImageMap() {
	if s.ImageObservations == nil {
		s.ImageObservations = map[string]string{}
	}
}

func observeImageWithModel(ctx context.Context, client *llm.Client, s *Session, image llm.ImageInput, question string) (string, error) {
	if client == nil {
		return "", fmt.Errorf("未配置可用的识图模型")
	}
	prompt := strings.TrimSpace(question)
	if prompt == "" {
		prompt = "请详细读取并说明这张图片的标题、坐标轴、单位、图例、关键数据变化及可见细节。"
	}
	msgs := []llm.Message{
		{Role: "system", Content: visionObservationSystem},
		{Role: "user", Content: prompt, Images: []llm.ImageInput{image}},
	}
	started := time.Now()
	text, _, usage, err := streamTurn(ctx, client, msgs, nil, func(Event) {})
	s.recordUsage(s.Turn, "vision", started, usage, err == nil)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(text), nil
}

// ensureImageObservations extracts structured visual facts via the dedicated vision model
// when the active conversation model is text-only or differs from the vision model.
func ensureImageObservations(ctx context.Context, cfg Config, s *Session, userText string, _ func(Event)) error {
	if !isCrossModelVision(cfg) {
		return nil
	}
	var images []*contracts.Manifest
	for _, m := range s.Manifests {
		if m.Modality == "image" && m.VisionPath != "" {
			images = append(images, m)
		}
	}
	if len(images) == 0 {
		return nil
	}
	s.ensureImageMap()
	for _, m := range images {
		if strings.TrimSpace(s.ImageObservations[m.ManifestID]) != "" {
			continue
		}
		q := "请完整提取这张图片中的标题、坐标轴、单位、图例、关键数值/趋势变化以及所有可见文字标注。"
		if strings.TrimSpace(userText) != "" && len(images) <= 2 {
			q += "\n\n用户当前重点关注的问题：" + strings.TrimSpace(userText)
		}
		mediaType := m.MediaType
		if mediaType == "" {
			mediaType = mediaTypeForExt(m.VisionPath)
		}
		obs, err := observeImageWithModel(ctx, cfg.VisionLLM, s, llm.ImageInput{
			Path:      m.VisionPath,
			MediaType: mediaType,
			Name:      m.OriginalName,
		}, q)
		if err != nil {
			log.Printf("session %s vision observation failed for %s (%s): %v", s.ID, m.ManifestID, cfg.VisionLLM.Model, err)
			continue
		}
		if obs != "" {
			s.ImageObservations[m.ManifestID] = obs
		}
	}
	return nil
}

func resolveInspectImageTarget(cfg Config, s *Session, targetID string) (llm.ImageInput, string, error) {
	targetID = strings.TrimSpace(targetID)
	if targetID == "" {
		return llm.ImageInput{}, "", fmt.Errorf("请提供 target_id（img_…、art_… 或工作区图片路径）")
	}
	for _, m := range s.Manifests {
		if m.ManifestID == targetID {
			if m.Modality != "image" {
				return llm.ImageInput{}, "", fmt.Errorf("%s 不是图片附件（当前模态：%s）", targetID, m.Modality)
			}
			path := m.VisionPath
			if path == "" {
				path = m.SourcePath
			}
			mediaType := m.MediaType
			if mediaType == "" {
				mediaType = mediaTypeForExt(path)
			}
			return llm.ImageInput{Path: path, MediaType: mediaType, Name: m.OriginalName}, "manifest", nil
		}
	}
	if strings.HasPrefix(targetID, "art_") && cfg.StorageDir != "" {
		rec, err := artifacts.Load(cfg.StorageDir, targetID)
		if err != nil {
			return llm.ImageInput{}, "", fmt.Errorf("未找到图表产物 %s：%v", targetID, err)
		}
		if rec.Kind != "plot" && !strings.HasPrefix(rec.MediaType, "image/") {
			return llm.ImageInput{}, "", fmt.Errorf("产物 %s（kind=%s）不是图像或图表，请使用 read_artifact 读取", targetID, rec.Kind)
		}
		uri := rec.URI
		mediaType := rec.MediaType
		if rep, ok := rec.Representations["png"]; ok && rep.URI != "" {
			uri = rep.URI
			mediaType = rep.MediaType
		}
		if mediaType == "" || mediaType == "image/svg+xml" || mediaType == "application/pdf" {
			mediaType = "image/png"
		}
		abs, err := filepath.Abs(uri)
		root, _ := filepath.Abs(cfg.StorageDir)
		rel, relErr := filepath.Rel(root, abs)
		if err != nil || relErr != nil || !filepath.IsLocal(rel) {
			return llm.ImageInput{}, "", fmt.Errorf("产物图像路径不在受控存储目录内")
		}
		return llm.ImageInput{Path: abs, MediaType: mediaType, Name: targetID + filepath.Ext(abs)}, "artifact", nil
	}
	if s.WorkspaceID != "" && cfg.StorageDir != "" {
		body, err := os.ReadFile(filepath.Join(cfg.StorageDir, "workspaces", s.WorkspaceID+".json"))
		if err != nil {
			return llm.ImageInput{}, "", fmt.Errorf("无法读取工作区记录：%v", err)
		}
		var ws struct {
			Root string `json:"root"`
		}
		if err := json.Unmarshal(body, &ws); err != nil || ws.Root == "" {
			return llm.ImageInput{}, "", fmt.Errorf("工作区根目录无效")
		}
		root, _ := filepath.Abs(ws.Root)
		candidate := targetID
		if !filepath.IsAbs(candidate) {
			candidate = filepath.Join(root, candidate)
		}
		abs, err := filepath.Abs(candidate)
		rel, relErr := filepath.Rel(root, abs)
		if err != nil || relErr != nil || !filepath.IsLocal(rel) {
			return llm.ImageInput{}, "", fmt.Errorf("文件路径 %s 不在当前工作区内", targetID)
		}
		if !isImageFileExt(abs) {
			return llm.ImageInput{}, "", fmt.Errorf("文件 %s 不是支持的静态图像格式（png/jpg/webp）", targetID)
		}
		return llm.ImageInput{Path: abs, MediaType: mediaTypeForExt(abs), Name: filepath.Base(abs)}, "workspace", nil
	}
	return llm.ImageInput{}, "", fmt.Errorf("未找到图像目标 %s；请确认 img_…、art_… 或已挂接工作区内的相对路径", targetID)
}

func executeInspectImage(ctx context.Context, cfg Config, s *Session, rawArgs json.RawMessage) (json.RawMessage, bool) {
	var args struct {
		TargetID string `json:"target_id"`
		Question string `json:"question"`
	}
	if err := json.Unmarshal(rawArgs, &args); err != nil {
		return errResult("bad_args", err.Error()), true
	}
	visionClient := cfg.VisionLLM
	if visionClient == nil || visionClient.Model == "" {
		visionClient = cfg.LLM
	}
	if visionClient == nil || visionClient.Model == "" {
		return errResult("vision_unavailable", "当前未配置可用于识图的模型，请先在模型选择器中指定看图模型"), true
	}
	img, kind, err := resolveInspectImageTarget(cfg, s, args.TargetID)
	if err != nil {
		return errResult("invalid_target", err.Error()), true
	}
	obs, err := observeImageWithModel(ctx, visionClient, s, img, args.Question)
	if err != nil {
		return errResult("vision_error", err.Error()), true
	}
	if kind == "manifest" {
		s.ensureImageMap()
		s.ImageObservations[args.TargetID] = obs
	}
	out, _ := json.Marshal(map[string]any{
		"status":       "ok",
		"target_id":    args.TargetID,
		"target_kind":  kind,
		"vision_model": visionClient.Model,
		"observation":  obs,
	})
	return out, false
}
