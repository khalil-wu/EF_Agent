"""Shared scientific figure defaults for tools, MNE figures and generated scripts.

Style changes presentation only. Overview sampling retains local extrema and leaves source values unchanged.
"""

from __future__ import annotations

from pathlib import Path

COLORS = ["#2E638A", "#BD704A", "#488675", "#9A73A5", "#8E6B22", "#688BA5", "#71747A"]
BAND_COLORS = {"delta": "#667D97", "theta": "#538B94", "alpha": "#50836B", "beta": "#A47B4F", "gamma": "#98687D"}
BAND_LABELS = {"delta": "δ 波", "theta": "θ 波", "alpha": "α 波", "beta": "β 波", "gamma": "γ 波"}
WIDTHS = {"single": 89 / 25.4, "wide": 183 / 25.4}
FONT_SIZES = {
    "screen": {"body": 9.5, "tick": 8.5, "title": 11, "small": 8, "legend": 8.5},
    "publication": {"body": 6.5, "tick": 6, "title": 7, "small": 6, "legend": 6},
}
LINE_STYLES = ["-", "--", "-.", ":"]
MEDIA_TYPES = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf"}


def peak_preserving_indices(values, max_points=6000):
    """Bound overview trace size while retaining each sample block's min/max and both endpoints."""
    import numpy as np

    size = len(values)
    if size <= max_points:
        return np.arange(size)
    block_size = int(np.ceil(size / (max_points // 2)))
    count = size // block_size
    blocks = values[:count * block_size].reshape(count, block_size)
    starts = np.arange(count) * block_size
    indices = np.column_stack((starts + np.argmin(blocks, axis=1),
                               starts + np.argmax(blocks, axis=1))).ravel()
    tail_start = count * block_size
    if tail_start < size:
        tail = values[tail_start:]
        indices = np.r_[indices, tail_start + np.argmin(tail), tail_start + np.argmax(tail)]
    return np.unique(np.r_[0, indices, size - 1])


def style(profile="screen"):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cycler, font_manager

    installed = {font.name for font in font_manager.fontManager.ttflist}
    fonts = [name for name in ("Microsoft YaHei", "Noto Sans SC", "Noto Sans CJK SC", "SimHei", "Arial", "Liberation Sans", "DejaVu Sans")
             if name in installed]
    sizes = FONT_SIZES[profile]
    plt.rcParams.update({
        "font.family": fonts[:1] + ["DejaVu Sans"], "font.size": sizes["body"], "mathtext.fontset": "dejavusans",
        "axes.titlesize": sizes["title"], "axes.titleweight": "normal", "axes.titlepad": 7,
        "axes.labelsize": sizes["body"], "axes.labelpad": 4, "axes.labelcolor": "#282D33",
        "text.color": "#282D33", "axes.edgecolor": "#626870", "axes.linewidth": .65,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": False,
        "axes.prop_cycle": cycler(color=COLORS), "axes.formatter.use_mathtext": profile != "publication",
        "xtick.color": "#626870", "ytick.color": "#626870",
        "xtick.labelsize": sizes["tick"], "ytick.labelsize": sizes["tick"],
        "xtick.top": False, "ytick.right": False,
        "xtick.direction": "out", "ytick.direction": "out",
        "xtick.major.size": 3, "ytick.major.size": 3,
        "xtick.major.width": .65, "ytick.major.width": .65,
        "xtick.minor.visible": False, "ytick.minor.visible": False,
        "grid.linewidth": .45, "grid.color": "#E6E9ED", "grid.alpha": 1,
        "lines.linewidth": .7 if profile == "publication" else 1.2, "lines.markersize": 3,
        "legend.fontsize": sizes["legend"], "legend.frameon": False, "legend.handlelength": 1.8,
        "figure.figsize": (WIDTHS["wide"], 3.6), "figure.facecolor": "white", "axes.facecolor": "white",
        "figure.dpi": 140, "savefig.dpi": 300, "savefig.pad_inches": .10,
        "svg.fonttype": "none", "pdf.fonttype": 42, "ps.fonttype": 42,
        "axes.unicode_minus": True,
    })
    return plt


def figure(nrows=1, ncols=1, *, width="wide", height=None, profile="screen", **kwargs):
    """Create axes at their final single-column (89 mm) or double-column (183 mm) width."""
    plt = style(profile)
    return plt.subplots(nrows, ncols, figsize=(WIDTHS[width], height or 2.7 * nrows),
                        layout="constrained", **kwargs)


def save_files(fig, png_path: Path) -> dict:
    """One figure, three representations; SVG text and PDF fonts remain editable."""
    files = {}
    for extension, media_type in MEDIA_TYPES.items():
        path = png_path.with_suffix("." + extension)
        fig.savefig(path, format=extension, dpi=300, bbox_inches=None, facecolor="white")
        files[extension] = {"uri": str(path), "media_type": media_type}
    return files


def unit_note(steps: list[str]) -> str:
    if any(step.startswith("normalize_") for step in steps):
        return "归一化数值（任意单位）"
    if "unit_unknown" in steps:
        return "原始数值单位"
    if "dataset_adapter" in steps or "unit_format_assumption" in steps:
        return "按格式约定使用 µV"
    if "unit_inferred" in steps:
        return "按数据尺度估计为 µV"
    return ""


def sampling_note(steps: list[str], sample_rate_hz: float) -> str:
    if "sample_rate_format_assumption" in steps:
        return f"按格式约定采样率：{sample_rate_hz:g} Hz"
    return ""
