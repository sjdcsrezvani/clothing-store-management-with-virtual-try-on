"""Output normalization for Instagram Studio (no network)."""
import io

from PIL import Image

from services.instagram_gen import TARGET_SIZES, normalize_image


def _jpeg(width: int, height: int, color=(230, 220, 210)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def _size(raw: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(raw)) as image:
        return image.size


def test_target_sizes_are_pinned():
    assert TARGET_SIZES == {"4:5": (1440, 1800), "1:1": (1440, 1440), "9:16": (1440, 2560)}


def test_every_aspect_normalizes_to_the_exact_pixel_size():
    raw = _jpeg(1024, 1024)
    for ratio, expected in TARGET_SIZES.items():
        assert _size(normalize_image(raw, ratio)) == expected, ratio


def test_landscape_input_is_center_cropped_to_portrait():
    raw = _jpeg(1600, 900, (10, 20, 30))
    out = normalize_image(raw, "4:5")
    assert _size(out) == (1440, 1800)


def test_logo_composite_changes_the_output(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (120, 60), (255, 0, 0, 255)).save(logo)
    clean = normalize_image(_jpeg(1024, 1024), "4:5")
    badged = normalize_image(_jpeg(1024, 1024), "4:5", logo=True, logo_path=str(logo))
    assert badged != clean
    assert _size(badged) == (1440, 1800)


def test_logo_toggle_without_a_file_keeps_the_clean_image():
    clean = normalize_image(_jpeg(1024, 1024), "1:1")
    same = normalize_image(_jpeg(1024, 1024), "1:1", logo=True, logo_path="")
    assert same == clean


def test_unknown_aspect_is_rejected():
    import pytest
    with pytest.raises(KeyError):
        normalize_image(_jpeg(100, 100), "16:9")


class _FakeResponse:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []
        self.urls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, **kwargs):
        self.posts.append(kwargs)
        self.urls.append(url)
        return self.responses.pop(0)


def test_seedream_retries_at_2k_when_the_custom_size_is_rejected(monkeypatch, tmp_path):
    import base64

    import services.instagram_gen as instagram_gen

    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg(200, 200))
    encoded = base64.b64encode(_jpeg(1024, 1024)).decode("ascii")
    client = _FakeClient([
        _FakeResponse(400, '{"error":{"message":"invalid size: 1440x1800"}}'),
        _FakeResponse(200, "", {"data": [{"b64_json": encoded}]}),
    ])
    monkeypatch.setattr(instagram_gen.httpx, "Client", lambda **kwargs: client)

    out = instagram_gen.generate_instagram_image(
        prompt="prompt", reference_paths=[str(reference)], aspect_ratio="4:5",
        provider="avalai", model="seedream-5-0-260128", api_key="sk-test",
    )

    assert client.posts[0]["json"]["size"] == "1440x1800"
    assert client.posts[1]["json"]["size"] == "2K"
    assert client.urls[0].endswith("/images/edits")
    assert _size(out) == (1440, 1800)


def test_gpt_edit_requests_use_the_gpt_size_for_every_aspect(monkeypatch, tmp_path):
    import base64

    import services.instagram_gen as instagram_gen
    from services.instagram_prompts import ASPECTS

    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg(200, 200))
    encoded = base64.b64encode(_jpeg(1024, 1024)).decode("ascii")

    for ratio, aspect in ASPECTS.items():
        client = _FakeClient([
            _FakeResponse(200, "", {"data": [{"b64_json": encoded}]}),
        ])
        monkeypatch.setattr(instagram_gen.httpx, "Client", lambda **kwargs: client)

        out = instagram_gen.generate_instagram_image(
            prompt="prompt", reference_paths=[str(reference)], aspect_ratio=ratio,
            provider="avalai", model="gpt-image-2.5-flare", api_key="sk-test",
        )

        assert client.urls[0].endswith("/images/edits")
        assert client.posts[0]["data"]["size"] == aspect["gpt_size"], ratio
        assert aspect["seedream_size"] not in client.posts[0]["data"].values(), ratio
        assert client.posts[0]["files"][0][0] == "image[]"
        assert _size(out) == TARGET_SIZES[ratio]


def test_text_generation_posts_json_without_references(monkeypatch):
    import base64

    import services.instagram_gen as instagram_gen

    encoded = base64.b64encode(_jpeg(1024, 1024)).decode("ascii")
    client = _FakeClient([_FakeResponse(200, "", {"data": [{"b64_json": encoded}]})])
    monkeypatch.setattr(instagram_gen.httpx, "Client", lambda **kwargs: client)

    out = instagram_gen.generate_instagram_image(
        prompt="campaign", reference_paths=[], aspect_ratio="4:5",
        provider="avalai", model="qwen-image-3.0-pro", api_key="sk-test",
    )

    assert client.urls[0].endswith("/images/generations")
    assert client.posts[0]["json"] == {
        "model": "qwen-image-3.0-pro",
        "prompt": "campaign",
        "n": 1,
        "size": "1152x1440",
    }
    assert client.posts[0].get("files") is None
    assert _size(out) == (1440, 1800)


def test_text_generation_uses_the_square_size_for_the_gapgpt_model(monkeypatch):
    import base64

    import services.instagram_gen as instagram_gen

    encoded = base64.b64encode(_jpeg(1024, 1024)).decode("ascii")
    client = _FakeClient([_FakeResponse(200, "", {"data": [{"b64_json": encoded}]})])
    monkeypatch.setattr(instagram_gen.httpx, "Client", lambda **kwargs: client)

    instagram_gen.generate_instagram_image(
        prompt="campaign", reference_paths=[], aspect_ratio="9:16",
        provider="gapgpt", model="gapgpt/qwen-image-2.1", api_key="sk-test",
    )

    assert client.urls[0].endswith("/images/generations")
    assert client.posts[0]["json"]["size"] == "1024x1024"


def test_text_generation_retries_once_at_square_when_the_size_is_rejected(monkeypatch):
    import base64

    import services.instagram_gen as instagram_gen

    encoded = base64.b64encode(_jpeg(1024, 1024)).decode("ascii")
    client = _FakeClient([
        _FakeResponse(400, '{"error":{"message":"invalid size"}}'),
        _FakeResponse(200, "", {"data": [{"b64_json": encoded}]}),
    ])
    monkeypatch.setattr(instagram_gen.httpx, "Client", lambda **kwargs: client)

    instagram_gen.generate_instagram_image(
        prompt="campaign", reference_paths=[], aspect_ratio="4:5",
        provider="avalai", model="qwen-image-3.0-pro", api_key="sk-test",
    )

    assert client.posts[0]["json"]["size"] == "1152x1440"
    assert client.posts[1]["json"]["size"] == "1024x1024"


def test_provider_errors_are_actionable_and_hide_raw_codes():
    from services.instagram_gen import _error_text

    cases = {
        "auth": "کلید",
        "model_unavailable": "کانال فعال",
        "content_policy": "خط‌مشی محتوا",
        "rate_limit": "محدودیت نرخ",
        "server": "خطای سرور",
        "network": "ارتباط",
        "no_image": "تصویری برنگرداند",
        "refusal": "تصویری برنگرداند",
        "no_op": "همان تصویر مرجع",
        "source_echo": "همان تصویر مرجع",
        "bad_response": "پاسخ ارائه‌دهنده",
        "bad_request": "درخواست ارائه‌دهنده رد شد",
    }
    for category, phrase in cases.items():
        text = _error_text("avalai", category, "secret_code_123", 400,
                           "raw provider detail")
        assert "secret_code_123" not in text, category
        assert "raw provider detail" not in text, category
        assert phrase in text, category


def test_a_missing_reference_file_is_refused_before_the_provider_call():
    import pytest
    from services.image_gen import ImageGenerationError
    from services.instagram_gen import generate_instagram_image

    with pytest.raises(ImageGenerationError) as error:
        generate_instagram_image(
            prompt="prompt", reference_paths=["static/uploads/does-not-exist.jpg"],
            aspect_ratio="4:5", provider="avalai", model="seedream-5-0-260128",
            api_key="sk-test",
        )
    assert error.value.code == "missing_reference"


def test_write_output_prunes_outputs_past_the_retention_window(tmp_path):
    import os
    import time

    import services.instagram_gen as instagram_gen

    old = tmp_path / "ig_old.jpg"
    old.write_bytes(b"old")
    stale = time.time() - (instagram_gen.OUTPUT_RETENTION_DAYS + 1) * 86400
    os.utime(old, (stale, stale))
    fresh = tmp_path / "ig_fresh.jpg"
    fresh.write_bytes(b"fresh")

    instagram_gen.write_output(_jpeg(64, 64), str(tmp_path / "ig_new.jpg"))

    assert not old.exists()
    assert fresh.exists()
    assert (tmp_path / "ig_new.jpg").exists()
