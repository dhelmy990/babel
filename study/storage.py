from pathlib import Path
import os
import uuid

from django.conf import settings


def private_media_root() -> Path:
    return Path(getattr(settings, "PRIVATE_MEDIA_ROOT", settings.MEDIA_ROOT))


def write_asset(data: bytes) -> str:
    key = f"assets/{uuid.uuid4().hex}"
    path = private_media_root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            path.unlink()
            path.parent.rmdir()
        except OSError:
            pass
        raise
    return key


def path_for(storage_key: str) -> Path:
    root = private_media_root().resolve()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise ValueError("Invalid storage key")
    return path


def remove_asset(storage_key: str) -> None:
    try:
        path = path_for(storage_key)
        path.unlink()
        root = private_media_root().resolve()
        parent = path.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    except FileNotFoundError:
        pass
