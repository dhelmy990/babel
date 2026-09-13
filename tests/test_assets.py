from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image


def png_bytes():
    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    return output.getvalue()


@pytest.mark.django_db
def test_published_image_is_private_and_available_via_asset_endpoint(publisher, client, settings, tmp_path):
    from study.services.content import publish_article

    settings.PRIVATE_MEDIA_ROOT = tmp_path
    article = publish_article(
        publisher, title="Images", color="#1a5276",
        markdown="# Images\n\n![blue](images/blue.png)", images={"images/blue.png": png_bytes()},
        submission_id=uuid4(),
    )
    asset = article.assets.get()

    response = client.get(f"/assets/{asset.pk}")
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_cleanup_removes_only_old_unreferenced_files(publisher, settings, tmp_path):
    import os
    from datetime import timedelta
    from django.core.management import call_command
    from django.utils import timezone
    from study.services.content import publish_article

    settings.PRIVATE_MEDIA_ROOT = tmp_path
    article = publish_article(
        publisher, title="Images", color="#1a5276", markdown="# Images\n\n![blue](images/blue.png)",
        images={"images/blue.png": png_bytes()}, submission_id=uuid4(),
    )
    referenced = tmp_path / article.assets.get().storage_key
    old = tmp_path / "assets" / "old-orphan"
    recent = tmp_path / "assets" / "recent-orphan"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old")
    recent.write_bytes(b"recent")
    old_time = (timezone.now() - timedelta(hours=25)).timestamp()
    os.utime(old, (old_time, old_time))

    call_command("cleanup_unreferenced_assets")

    assert referenced.exists()
    assert not old.exists()
    assert recent.exists()


def test_failed_low_level_asset_write_removes_its_partial_file(monkeypatch, settings, tmp_path):
    import os
    from study.storage import write_asset

    settings.PRIVATE_MEDIA_ROOT = tmp_path
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk failure")))

    with pytest.raises(OSError, match="disk failure"):
        write_asset(b"partial")
    assert not list(tmp_path.rglob("*"))
