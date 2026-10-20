"""Score predictions against independent reference labels, intervals and epochs.

The caller supplies reference data from a documented source_truth. An LLM's own
assessment, rationale or saved label is a prediction and cannot supply that truth.
Synthetic EEG fixtures in this project have no emotion reference labels.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment


def classification_scores(reference, prediction, classes):
    if len(reference) != len(prediction):
        raise ValueError("预测标签数量必须与参考标签数量一致")
    lookup = {label: index for index, label in enumerate(classes)}
    matrix = np.zeros((len(classes), len(classes)), dtype=int)
    for expected, actual in zip(reference, prediction):
        matrix[lookup[expected], lookup[actual]] += 1
    true_positive = np.diag(matrix)
    denominator = matrix.sum(axis=0) + matrix.sum(axis=1)
    f1 = np.divide(2 * true_positive, denominator, out=np.zeros(len(classes)), where=denominator > 0)
    return {"accuracy": float(true_positive.sum() / matrix.sum()), "macro_f1": float(f1.mean()),
            "classes": classes, "confusion_matrix": matrix.tolist()}


def sleep_scores(reference, prediction):
    result = classification_scores(reference, prediction, ["W", "N1", "N2", "N3", "REM"])
    matrix = np.array(result["confusion_matrix"])
    expected_agreement = float(matrix.sum(axis=0) @ matrix.sum(axis=1) / matrix.sum() ** 2)
    result["cohen_kappa"] = ((result["accuracy"] - expected_agreement) / (1 - expected_agreement)
                             if expected_agreement < 1 else None)
    result["n_epochs"] = len(reference)
    return result


def interval_scores(reference, prediction, min_iou=.5):
    """One-to-one interval-IoU F1; this is not the SzCORE tolerance-based metric."""
    expected = np.asarray(reference, dtype=float).reshape(-1, 2)
    actual = np.asarray(prediction, dtype=float).reshape(-1, 2)
    for values in (expected, actual):
        if len(values) and np.any(values[:, 1] <= values[:, 0]):
            raise ValueError("区间结束时间必须大于开始时间")
    matches = []
    if len(expected) and len(actual):
        overlap = np.maximum(0, np.minimum(expected[:, None, 1], actual[None, :, 1]) -
                             np.maximum(expected[:, None, 0], actual[None, :, 0]))
        union = np.diff(expected, axis=1) + np.diff(actual, axis=1).T - overlap
        iou = overlap / union
        rows, columns = linear_sum_assignment((iou >= min_iou) * (max(len(expected), len(actual)) + 1) + iou, maximize=True)
        matches = [(int(i), int(j)) for i, j in zip(rows, columns) if iou[i, j] >= min_iou]
    tp = len(matches)
    fp, fn = len(actual) - tp, len(expected) - tp
    errors = [np.abs(expected[i] - actual[j]).tolist() for i, j in matches]
    return {"interval_iou_f1": 2 * tp / (2 * tp + fp + fn) if len(expected) + len(actual) else 1.0,
            "true_positive": tp, "false_positive": fp, "false_negative": fn, "matches": matches,
            "boundary_mae_s": float(np.mean(errors)) if errors else None,
            "max_boundary_error_s": float(np.max(errors)) if errors else None, "min_iou": min_iou}


def main():
    parser = argparse.ArgumentParser(description="Compare structured predictions with independent reference outcomes")
    parser.add_argument("--kind", choices=["classification", "events", "sleep"], required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    if args.kind == "events":
        result = interval_scores(reference["intervals"], prediction["intervals"])
    elif args.kind == "sleep":
        result = sleep_scores(reference["labels"], prediction["labels"])
    else:
        result = classification_scores(reference["labels"], prediction["labels"], reference["classes"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
