"""Instagram jobs are claimed and processed by the shared image worker."""
import asyncio
import io
import json

from PIL import Image

from models import BackgroundJob
from services.jobs import claim_next, enqueue, process_one


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 180, 160)).save(buffer, "JPEG")
    return buffer.getvalue()


def test_claim_next_accepts_a_tuple_of_image_job_types(db_session):
    db_session.add_all([
        BackgroundJob(job_type="sms", payload="{}", status="pending"),
        BackgroundJob(job_type="instagram_image", payload="{}", status="pending"),
    ])
    db_session.commit()

    claimed = claim_next(db_session, ("tryon", "instagram_image"))
    assert claimed is not None
    assert claimed.job_type == "instagram_image"


def test_process_one_instagram_writes_output(db_session, tmp_path, monkeypatch):
    import services.image_gen as image_gen
    import services.instagram_gen as instagram_gen

    output = tmp_path / "out.jpg"
    job = enqueue(db_session, "instagram_image", {
        "prompt": "a prompt",
        "reference_paths": [],
        "aspect_ratio": "4:5",
        "output_path": str(output),
        "result_url": "/static/uploads/instagram/out.jpg",
        "logo": False,
        "logo_path": "",
    })
    db_session.commit()
    claimed = claim_next(db_session, "instagram_image")

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return _jpeg()

    monkeypatch.setattr(image_gen, "get_generation_selection",
                        lambda db: ("avalai", "qwen-image-3.0-pro", "sk-gen"))
    monkeypatch.setattr(image_gen, "get_tryon_selection",
                        lambda db: ("avalai", "seedream-5-0-260128", "sk-edit"))
    monkeypatch.setattr(instagram_gen, "generate_instagram_image", fake_generate)

    assert asyncio.run(process_one(db_session, claimed)) is True
    assert captured["provider"] == "avalai"
    assert captured["model"] == "qwen-image-3.0-pro"
    assert captured["api_key"] == "sk-gen"
    assert output.exists()
    assert claimed.result_path == str(output)
    assert claimed.result_url == "/static/uploads/instagram/out.jpg"


def test_process_one_instagram_with_references_uses_the_edit_selection(
        db_session, tmp_path, monkeypatch):
    import services.image_gen as image_gen
    import services.instagram_gen as instagram_gen

    job = enqueue(db_session, "instagram_image", {
        "prompt": "a prompt",
        "reference_paths": ["static/uploads/ref.jpg"],
        "aspect_ratio": "4:5",
        "output_path": str(tmp_path / "out.jpg"),
        "result_url": "/static/uploads/instagram/out.jpg",
    })
    db_session.commit()
    claimed = claim_next(db_session, "instagram_image")

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return _jpeg()

    monkeypatch.setattr(image_gen, "get_generation_selection",
                        lambda db: ("avalai", "qwen-image-3.0-pro", "sk-gen"))
    monkeypatch.setattr(image_gen, "get_tryon_selection",
                        lambda db: ("avalai", "seedream-5-0-260128", "sk-edit"))
    monkeypatch.setattr(instagram_gen, "generate_instagram_image", fake_generate)

    assert asyncio.run(process_one(db_session, claimed)) is True
    assert captured["model"] == "seedream-5-0-260128"
    assert captured["api_key"] == "sk-edit"


def test_worker_alias_still_exists_for_existing_callers():
    import services.tryon_worker as worker

    assert worker.tryon_worker_task is worker.image_worker_task
    assert worker.IMAGE_JOB_TYPES == ("tryon", "instagram_image")


def test_instagram_quota_reserves_pending_and_frees_failures(db_session):
    from datetime import datetime, timedelta, timezone
    from services.security import instagram_daily_remaining

    today = datetime.now(timezone.utc)
    db_session.add_all([
        BackgroundJob(job_type="instagram_image", payload="{}", status="pending", created_at=today),
        BackgroundJob(job_type="instagram_image", payload="{}", status="processing", created_at=today),
        BackgroundJob(job_type="instagram_image", payload="{}", status="completed", created_at=today),
        BackgroundJob(job_type="instagram_image", payload="{}", status="failed", created_at=today),
        BackgroundJob(job_type="instagram_image", payload="{}", status="completed",
                      created_at=today - timedelta(days=2)),
        BackgroundJob(job_type="tryon", payload="{}", status="completed", created_at=today),
    ])
    db_session.commit()

    assert instagram_daily_remaining(db_session) == 17


def test_process_one_instagram_removes_the_uploaded_source_photo(
        db_session, tmp_path, monkeypatch):
    import services.image_gen as image_gen
    import services.instagram_gen as instagram_gen

    source = tmp_path / "source.jpg"
    source.write_bytes(_jpeg())
    job = enqueue(db_session, "instagram_image", {
        "prompt": "a prompt",
        "reference_paths": [str(source)],
        "temporary_paths": [str(source)],
        "aspect_ratio": "4:5",
        "output_path": str(tmp_path / "out.jpg"),
        "result_url": "/static/uploads/instagram/out.jpg",
    })
    db_session.commit()
    claimed = claim_next(db_session, "instagram_image")

    monkeypatch.setattr(image_gen, "get_generation_selection",
                        lambda db: ("avalai", "qwen-image-3.0-pro", "sk-gen"))
    monkeypatch.setattr(image_gen, "get_tryon_selection",
                        lambda db: ("avalai", "seedream-5-0-260128", "sk-edit"))
    monkeypatch.setattr(instagram_gen, "generate_instagram_image",
                        lambda **kwargs: _jpeg())

    assert asyncio.run(process_one(db_session, claimed)) is True
    assert not source.exists()
