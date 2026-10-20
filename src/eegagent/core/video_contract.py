"""Verify a video's native frame clock before applying the trained CFR recipes."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess


def probe_video(path: str | Path, require_cfr: bool = False) -> dict:
    result = {"duration_s": None, "fps": None, "width": None, "height": None, "audio": None,
              "timing": {"status": "unknown", "reasons": [], "first_pts_s": None,
                         "n_frames": None, "clock_basis": None}, "warnings": []}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        result["timing"]["reasons"].append("未检测到 ffprobe，无法核验视频逐帧时间轴")
    else:
        metadata = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-show_entries",
                                   "format=format_name:stream=codec_type,width,height,r_frame_rate,time_base",
                                   "-of", "json", str(path)], capture_output=True, text=True)
        if metadata.returncode or metadata.stderr.strip():
            result["timing"].update(status="unsupported", reasons=["视频流无法解码或媒体容器损坏"])
        else:
            info = json.loads(metadata.stdout)
            streams = info["streams"]
            videos = [stream for stream in streams if stream["codec_type"] == "video"]
            result["audio"] = any(stream["codec_type"] == "audio" for stream in streams)
            if not videos:
                result["timing"].update(status="unsupported", reasons=["媒体文件没有视频流"])
            else:
                stream = videos[0]
                result.update(width=stream["width"], height=stream["height"])
                frames = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                    "-show_frames", "-show_packets", "-show_entries",
                    "frame=pts,pkt_pts,best_effort_timestamp,duration,pkt_duration:packet=pts,dts,duration",
                    "-of", "json", str(path)], capture_output=True, text=True)
                if frames.returncode or frames.stderr.strip():
                    result["timing"].update(status="unsupported", reasons=["视频逐帧解码失败"])
                else:
                    _read_clock(result, info, stream, json.loads(frames.stdout)["packets_and_frames"])
    result["warnings"] = list(result["timing"]["reasons"])
    if require_cfr and result["timing"]["status"] != "cfr_verified":
        raise ValueError("; ".join(result["timing"]["reasons"]))
    return result


def _read_clock(result: dict, info: dict, stream: dict, rows: list[dict]) -> None:
    frames = [row for row in rows if row["type"] == "frame"]
    packets = [row for row in rows if row["type"] == "packet"]
    timing = result["timing"]
    timing.update(status="unsupported", n_frames=len(frames), origin="first_decoded_frame")
    if len(frames) < 2:
        timing["reasons"].append("视频至少需要两帧才能核验恒定帧时钟")
        return
    rate = stream["r_frame_rate"]
    if rate in ("0/0", "0/1"):
        timing["reasons"].append("视频未提供可核验的 native 帧时钟")
        return
    fps = Fraction(rate)
    tick = Fraction(stream["time_base"])
    period = 1 / fps
    timing["time_base_s"] = float(tick)
    first = frames[0].get("pts", frames[0].get("pkt_pts", frames[0].get("best_effort_timestamp")))
    timing["first_pts_s"] = float(first * tick) if first is not None else None

    # AVI has a native rate/scale frame-index clock, often no stored PTS. The
    # recovered DEAP AVI's B-frame best-effort PTS starts at .04s and is absent
    # on its final two decoded frames. Verify its per-frame packet clock rather
    # than treating those decoder estimates as stored presentation timestamps.
    avi_index_clock = info["format"]["format_name"] == "avi" and all("pts" not in row for row in packets)
    if avi_index_clock:
        cadence = period / tick
        constant = (len(packets) == len(frames) and all("dts" in row and "duration" in row for row in packets)
                    and all(row["duration"] == cadence for row in packets)
                    and all(row["dts"] - packets[0]["dts"] == index * cadence
                            for index, row in enumerate(packets)))
        timing.update(clock_basis="avi_native_frame_index", pts_kind="ffprobe_best_effort")
    else:
        timing.update(clock_basis="decoded_frame_pts", pts_kind="stored_pts")
        # FFmpeg 4 exposes the stored decoded-frame PTS as pkt_pts; newer
        # versions call the same field pts. Both are actual presentation PTS.
        pts = [row.get("pts", row.get("pkt_pts")) for row in frames]
        if any(value is None for value in pts):
            timing["reasons"].append("视频缺少逐帧 PTS，不能按平均帧率推算时间")
            return
        durations = [row.get("duration", row.get("pkt_duration")) for row in frames]
        if durations[-1] is not None:
            result["duration_s"] = float((pts[-1] - pts[0] + durations[-1]) * tick)
        errors = [abs((value - pts[0]) * tick - index * period) for index, value in enumerate(pts)]
        errors += [abs(duration * tick - period) for duration in durations if duration is not None]
        # Timestamp quantization cannot excuse an entire missing source frame.
        constant = (all(right > left for left, right in zip(pts, pts[1:])) and
                    all(error <= tick and error < period / 2 for error in errors))
    if not constant:
        timing["reasons"].append("视频为变帧率或不连续时间轴；当前训练抽帧规则仅支持恒定帧率视频")
        return
    timing["status"] = "cfr_verified"
    result.update(fps=float(fps), duration_s=float(len(frames) / fps))
