"""Native clock and participant boundaries, without model weights or GPU work."""
import ast
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image
import pytest

from eegagent.core.video_contract import probe_video, _read_clock


ROOT = Path(__file__).parents[1]


def test_coarse_time_base_does_not_hide_a_missing_entire_frame():
    result = {"timing": {"reasons": []}, "duration_s": None}
    rows = [{"type": "frame", "pts": pts, "pkt_duration": 1} for pts in [0, 1, 3, 4]]
    _read_clock(result, {"format": {"format_name": "mov"}},
                {"r_frame_rate": "25/1", "time_base": "1/25"}, rows)
    assert result["timing"]["status"] == "unsupported"
    assert result["duration_s"] == .2


def mocked_probe(monkeypatch, pts, *, first=0, container="mov,mp4,m4a,3gp,3g2,mj2", missing_pts=False, pts_key="pts"):
    monkeypatch.setattr(shutil, "which", lambda _: "ffprobe")
    metadata = {"streams": [{"codec_type": "video", "width": 32, "height": 24,
                             "r_frame_rate": "25/1", "time_base": "1/1000"},
                            {"codec_type": "audio", "duration": "99999"}],
                "format": {"format_name": container, "duration": "99999"}}
    rows = [{"type": "packet", "dts": first + i * 40, "duration": 40} for i in range(len(pts))]
    rows += [{"type": "frame", "best_effort_timestamp": first + value,
              **({} if missing_pts else {pts_key: first + value}), "pkt_duration": 40} for value in pts]
    replies = iter([metadata, {"packets_and_frames": rows}])
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: SimpleNamespace(
        returncode=0, stdout=json.dumps(next(replies)), stderr=""))


@pytest.mark.parametrize("pts_key", ["pts", "pkt_pts"])
def test_offset_pts_are_normalized_and_audio_duration_is_not_video_duration(monkeypatch, pts_key):
    mocked_probe(monkeypatch, [0, 40, 80, 120], first=5000, pts_key=pts_key)
    info = probe_video("offset.mp4", require_cfr=True)
    assert info["timing"]["status"] == "cfr_verified"
    assert info["timing"]["first_pts_s"] == 5
    assert info["timing"]["origin"] == "first_decoded_frame"
    assert info["duration_s"] == .16
    assert info["fps"] == 25 and info["audio"] is True


@pytest.mark.parametrize("pts", [[0, 40, 100, 140], [0, 40, 40, 80], [0, 80, 40, 120]])
def test_vfr_and_discontinuous_pts_do_not_become_average_fps(monkeypatch, pts):
    mocked_probe(monkeypatch, pts)
    info = probe_video("vfr.mp4")
    assert info["timing"]["status"] == "unsupported"
    assert info["fps"] is None
    assert "变帧率" in info["warnings"][0]


def test_missing_pts_in_other_containers_does_not_become_avi_clock(monkeypatch):
    mocked_probe(monkeypatch, [0, 40, 80], missing_pts=True)
    with pytest.raises(ValueError, match="缺少逐帧 PTS"):
        probe_video("missing.mp4", require_cfr=True)


def test_missing_tool_and_failed_decode_keep_explicit_states(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    info = probe_video("unknown.mp4")
    assert info["timing"]["status"] == "unknown" and info["fps"] is None
    with pytest.raises(ValueError, match="ffprobe"):
        probe_video("unknown.mp4", require_cfr=True)
    monkeypatch.setattr(shutil, "which", lambda _: "ffprobe")
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=1, stderr="decode error"))
    assert probe_video("broken.mp4")["timing"]["status"] == "unsupported"


@pytest.mark.parametrize("suffix,codec", [("mp4", "mpeg4"), ("mov", "mpeg4"), ("mkv", "mpeg4"),
                                         ("avi", "mpeg4"), ("webm", "libvpx-vp9"), ("flv", "flv1")])
def test_real_cfr_containers(tmp_path, suffix, codec):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe are needed for real media fixtures")
    video = tmp_path / f"cfr.{suffix}"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=32x32:rate=25:duration=1",
                    "-an", "-c:v", codec, "-pix_fmt", "yuv420p", str(video)], check=True)
    info = probe_video(video, require_cfr=True)
    assert info["duration_s"] == 1 and info["fps"] == 25
    assert info["timing"]["n_frames"] == 25


def test_real_recovered_avi_native_clock():
    video = ROOT / "output/deap-1s-integration-20261012/sources/s01_trial08_original.avi"
    if not video.exists() or not shutil.which("ffprobe"):
        pytest.skip("real DEAP audit source and ffprobe are required")
    info = probe_video(video, require_cfr=True)
    assert info["duration_s"] == 60 and info["fps"] == 50
    assert info["timing"]["clock_basis"] == "avi_native_frame_index"
    assert info["timing"]["n_frames"] == 3000
    assert info["timing"]["first_pts_s"] == .04


def test_real_vfr_and_offset_pts(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe are needed for real media fixtures")
    shifted, variable = tmp_path / "offset.mp4", tmp_path / "vfr.mkv"
    source = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=32x32:rate=25:duration=1", "-an"]
    subprocess.run([*source, "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-output_ts_offset", "5", str(shifted)], check=True)
    subprocess.run([*source, "-vf", "setpts=(N+floor(N/2))/(25*TB)", "-fps_mode", "vfr",
                    "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(variable)], check=True)
    offset = probe_video(shifted, require_cfr=True)
    assert offset["timing"]["first_pts_s"] == 5 and offset["duration_s"] == 1
    vfr = probe_video(variable)
    assert vfr["timing"]["status"] == "unsupported" and vfr["fps"] is None


class Capture:
    def __init__(self, fps=50, count=200):
        self.fps, self.count, self.index, self.released = fps, count, 0, False

    def isOpened(self):
        return True

    def get(self, prop):
        return self.fps if prop == cv2.CAP_PROP_FPS else self.count

    def set(self, prop, index):
        assert prop == cv2.CAP_PROP_POS_FRAMES
        self.index = int(index)

    def read(self):
        return True, np.full((24, 32, 3), self.index % 256, dtype=np.uint8)

    def release(self):
        self.released = True


def prepare_function(name, capture, n_faces=1):
    file = ROOT / "scripts/remote" / name
    node = next(node for node in ast.parse(file.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == "prepare_video")
    bbox = SimpleNamespace(xmin=.25, ymin=.25, width=.5, height=.5)
    face = SimpleNamespace(location_data=SimpleNamespace(relative_bounding_box=bbox), score=[.99])
    detector = SimpleNamespace(process=lambda _: SimpleNamespace(detections=[face] * n_faces), close=lambda: None)
    fake_cv2 = SimpleNamespace(**{key: getattr(cv2, key) for key in
        ["CAP_PROP_FPS", "CAP_PROP_FRAME_COUNT", "CAP_PROP_POS_FRAMES", "COLOR_BGR2RGB", "INTER_AREA",
         "IMWRITE_JPEG_QUALITY", "cvtColor", "resize", "imencode"]}, VideoCapture=lambda _: capture)
    clock = {"fps": capture.fps, "timing": {"status": "cfr_verified", "n_frames": capture.count}}
    scope = {"cv2": fake_cv2, "np": np, "BytesIO": BytesIO, "Image": Image, "Path": Path,
             "probe_video": lambda *_, **__: clock,
             "mp": SimpleNamespace(solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=lambda **_: detector)))}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(file), "exec"), scope)
    return scope["prepare_video"]


@pytest.mark.parametrize("n_faces,error", [(0, "No qualifying participant face"), (2, "Multiple qualifying faces")])
def test_deap_requires_exactly_one_face(n_faces, error):
    capture = Capture()
    prepare = prepare_function("camer_deap_1s_infer.py", capture, n_faces)
    with pytest.raises(ValueError, match=error):
        prepare("face.avi", [{"video_start_s": 0, "video_end_s": 1}], {})
    assert capture.released


def test_deap_requires_four_distinct_real_candidates():
    capture = Capture(fps=2, count=8)
    prepare = prepare_function("camer_deap_1s_infer.py", capture)
    with pytest.raises(ValueError, match="Only 2 real candidate frames"):
        prepare("lowfps.avi", [{"video_start_s": 0, "video_end_s": 1}], {})
    assert capture.released


def test_deap_cfr_sampling_and_crop_recipe_are_unchanged():
    capture = Capture()
    prepare = prepare_function("camer_deap_1s_infer.py", capture)
    images, info = prepare("face.avi", [{"video_start_s": 0, "video_end_s": 1}], {})
    assert info["frame_times_ms"] == [[0, 260, 520, 780]]
    assert info["face_boxes_xywh"] == [[[5, 4, 22, 16]] * 4]
    assert images.shape == (1, 4, 224, 224, 3)
    assert capture.released


def test_eav_does_not_repeat_lowfps_frames():
    capture = Capture(fps=1, count=5)
    prepare = prepare_function("camer_eav_infer.py", capture)
    with pytest.raises(ValueError, match="six distinct real source frames"):
        prepare("lowfps.mp4", [{"video_start_s": 0, "video_end_s": 5}])
    assert capture.released


def test_eav_preserves_full_frame_six_image_recipe():
    capture = Capture(fps=30, count=630)
    prepare = prepare_function("camer_eav_infer.py", capture)
    images, info = prepare("eav.mp4", [{"video_start_s": 0, "video_end_s": 5}])
    assert info["frame_times_ms"] == [[0, 750, 1750, 2750, 3750, 4750]]
    assert info["source_frame_indices"] == [[0, 23, 53, 83, 113, 143]]
    assert info["crop"] == "none; original training used full source frames"
    assert images.shape == (1, 6, 224, 224, 3)
    assert capture.released
