"""Filesystem helpers for safe filenames and ZIP archives."""

import logging
import re
import uuid
import zipfile
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def get_safe_filename(
    value: str, *, fallback: str = "documents", max_length: int = 120
) -> str:
    """Return a filesystem-safe ASCII filename component.

    Falls back to a sanitized ``fallback`` when ``value`` is empty after
    sanitizing, and to a random id when no usable fallback is provided either.
    The result is capped at ``max_length`` characters.
    """
    safe = _UNSAFE.sub("_", value).strip("._")
    if not safe:
        logger.debug(
            "filename %r sanitized to empty; using fallback %r", value, fallback
        )
        safe = _UNSAFE.sub("_", fallback).strip("._")
    if not safe:
        safe = str(uuid.uuid7())
        logger.debug("filename %r had no usable fallback; generated %s", value, safe)
    if len(safe) > max_length:
        logger.debug("filename %r trimmed to %d characters", value, max_length)
        safe = safe[:max_length].strip("._")
    return safe or str(uuid.uuid7())


def zip_files(file_paths: Iterable[str | Path], archive_path: Path) -> Path:
    """Bundle ``file_paths`` into a ZIP at ``archive_path``.

    Duplicate entry names are disambiguated with a numeric suffix. Missing
    inputs raise ``FileNotFoundError``. Returns the archive path.
    """
    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [Path(file_path) for file_path in file_paths]
    logger.debug("creating archive %s from %d file(s)", archive_path, len(paths))

    used_names: set[str] = set()
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in paths:
            if not path.is_file():
                logger.error("cannot archive missing file %s", path)
                raise FileNotFoundError(f"file not found: {path}")
            arcname = path.name
            stem = path.stem
            suffix = path.suffix
            duplicate = 2
            while arcname in used_names:
                arcname = f"{stem}_{duplicate}{suffix}"
                duplicate += 1
            if arcname != path.name:
                logger.debug("renamed duplicate entry %s -> %s", path.name, arcname)
            used_names.add(arcname)
            archive.write(path, arcname=arcname)

    logger.info("created archive %s with %d file(s)", archive_path, len(paths))
    return archive_path
