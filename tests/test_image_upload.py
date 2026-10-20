from pathlib import Path

import pytest
from PIL import Image

from eegagent.worker import attachments, store
from eegagent.worker.tools import run_tool


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    directory = store.ensure_layout() / "uploads" / "ses_images"
    directory.mkdir(parents=True)
    return directory


@pytest.mark.parametrize("extension,format,media_type", [
    ("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp"),
])
def test_images_are_registered_with_real_pixels_and_not_as_eeg_or_documents(uploads, monkeypatch, extension, format, media_type):
    path = uploads / f"分析图.{extension}"
    Image.new("RGB", (240, 120), "blue").save(path, format=format)
    monkeypatch.setattr(attachments, "parse_document", lambda *a: pytest.fail("image entered the document parser"))
    result = run_tool("register_upload", {"path": str(path), "_session_id": "ses_images"})
    manifest = result["manifest"]
    assert manifest["modality"] == "image"
    assert (manifest["width"], manifest["height"]) == (240, 120)
    assert manifest["media_type"] == media_type
    assert Path(manifest["vision_path"]).read_bytes() == path.read_bytes()
    assert "document_index" not in manifest
    assert store.load_manifest(manifest["manifest_id"])["source_path"] == str(path)


def test_gif_uses_a_real_static_frame_while_preserving_the_original(uploads):
    path = uploads / "图形.gif"
    first = Image.new("RGB", (24, 18), "red")
    first.save(path, save_all=True, append_images=[Image.new("RGB", first.size, "blue")], duration=100, loop=0)
    manifest = attachments.register_upload(str(path))["manifest"]
    assert manifest["frame_count"] == 2 and manifest["media_type"] == "image/png"
    assert manifest["source_path"] == str(path)
    with Image.open(manifest["vision_path"]) as image:
        assert image.format == "PNG" and image.getpixel((0, 0))[:3] == (255, 0, 0)


def test_rotated_photo_is_oriented_before_provider_delivery(uploads):
    path = uploads / "旋转照片.jpg"
    image = Image.new("RGB", (30, 20), "green")
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    manifest = attachments.register_upload(str(path))["manifest"]
    assert (manifest["width"], manifest["height"]) == (20, 30)
    with Image.open(manifest["vision_path"]) as oriented:
        assert oriented.size == (20, 30)
    assert list(uploads.iterdir()) == [path]  # Derived viewing pixels stay in storage, outside the source directory.


def test_image_extension_does_not_turn_invalid_bytes_into_a_picture(uploads):
    path = uploads / "invalid.png"
    path.write_text("This is not an image", encoding="utf-8")
    with pytest.raises(OSError):
        attachments.register_upload(str(path))
