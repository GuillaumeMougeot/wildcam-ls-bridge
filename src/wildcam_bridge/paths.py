"""Safe path handling.

The CSV comes from outside, and its ``file`` column decides both which remote file is read
and where it is written locally. Every value is validated so a malicious or broken row
cannot escape the job folder (``../../.ssh/authorized_keys``) or the ERDA share.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from wildcam_bridge.settings import Settings

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# Fixed-length job ids (see jobs.new_job_id). Label Studio grants file access by a plain
# string-prefix match on the storage path, so ids must never be prefixes of each other.
JOB_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")


class UnsafePathError(ValueError):
    """Raised when a path from user input is not a clean relative POSIX path."""


def sanitize_relative(value: str) -> PurePosixPath:
    """Return ``value`` as a normalised relative POSIX path or raise ``UnsafePathError``."""
    if not value or not value.strip():
        raise UnsafePathError("empty path")
    if _CONTROL_CHARS.search(value):
        raise UnsafePathError(f"control character in path: {value!r}")
    if "\\" in value:
        raise UnsafePathError(f"backslash in path: {value!r}")
    if value.startswith("/"):
        raise UnsafePathError(f"absolute path not allowed: {value!r}")
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafePathError(f"empty path: {value!r}")
    if any(p == ".." for p in parts):
        raise UnsafePathError(f"'..' not allowed in path: {value!r}")
    return PurePosixPath(*parts)


def strip_prefix(value: str, prefix: str) -> str:
    """Remove ``prefix`` (if present) from the start of a CSV path."""
    if prefix and value.startswith(prefix):
        return value[len(prefix) :]
    return value


def remote_path(rel_path: PurePosixPath, remote_root: str) -> str:
    """Path on the ERDA share for a sanitised relative media path."""
    root = remote_root.strip("/")
    if root:
        sanitize_relative(root)
        return f"{root}/{rel_path.as_posix()}"
    return rel_path.as_posix()


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.match(job_id):
        raise UnsafePathError(f"invalid job id: {job_id!r}")
    return job_id


def job_media_dir(settings: Settings, job_id: str) -> Path:
    """Folder (inside the Label Studio document root) that receives a job's videos."""
    return settings.jobs_media_root / validate_job_id(job_id)


def local_media_path(settings: Settings, job_id: str, rel_path: PurePosixPath) -> Path:
    base = job_media_dir(settings, job_id).resolve()
    target = (base / Path(*rel_path.parts)).resolve()
    if not target.is_relative_to(base):  # defence in depth (symlinks)
        raise UnsafePathError(f"path escapes job folder: {rel_path}")
    return target


def ls_local_files_url(settings: Settings, job_id: str, rel_path: PurePosixPath) -> str:
    """URL Label Studio uses to stream a file from LOCAL_FILES_DOCUMENT_ROOT.

    Format documented in the team guide: ``/data/local-files/?d=<path relative to doc root>``.
    The path is percent-encoded so spaces, '#', '&' or non-ASCII names survive.
    """
    rel = f"{settings.ls_data_subdir}/{validate_job_id(job_id)}/{rel_path.as_posix()}"
    return "/data/local-files/?d=" + quote(rel, safe="/")
