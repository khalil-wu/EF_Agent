"""Label meaning is part of the result contract, shared by predictors and assessments."""

QUADRANTS = {
    "HVHA": ("high", "high", "高效价、高唤醒"),
    "HVLA": ("high", "low", "高效价、低唤醒"),
    "LVHA": ("low", "high", "低效价、高唤醒"),
    "LVLA": ("low", "low", "低效价、低唤醒"),
}
ALIASES = {"HAHV": "HVHA", "LAHV": "HVLA", "HALV": "LVHA", "LALV": "LVLA"}
EMOTIONS = {"neutral": "中性", "sad": "悲伤", "anger": "愤怒", "happy": "愉快", "calm": "平静"}


def label_definitions(labels: list[str]) -> list[dict]:
    definitions = []
    for label in labels:
        canonical = ALIASES.get(label, label)
        if canonical in QUADRANTS:
            valence, arousal, text = QUADRANTS[canonical]
            definitions.append({"label": label, "label_text": text, "valence": valence, "arousal": arousal})
        else:
            definitions.append({"label": label, "label_text": EMOTIONS.get(label, label)})
    return definitions
