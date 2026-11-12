from pathlib import PurePosixPath

import pytest

from wildcam_bridge.paths import (
    UnsafePathError,
    job_media_dir,
    local_media_path,
    ls_local_files_url,
    remote_path,
    sanitize_relative,
    strip_prefix,
    validate_job_id,
)
from wildcam_bridge.settings import Settings

JOB = "20261112T120000Z-abcdef"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("data/a/b.mp4", "data/a/b.mp4"),
        ("./data//a/./b.mp4", "data/a/b.mp4"),
        ("Lille Vildmose/Høstemark_0001/IMG 1.MP4", "Lille Vildmose/Høstemark_0001/IMG 1.MP4"),
    ],
)
def test_sanitize_accepts_clean_relative_paths(raw: str, expected: str) -> None:
    assert sanitize_relative(raw) == PurePosixPath(expected)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "/etc/passwd", "../x.mp4", "data/../../x", "a\\b.mp4", "a/\x00b", "a\nb", "./."],
)
def test_sanitize_rejects_unsafe_paths(raw: str) -> None:
    with pytest.raises(UnsafePathError):
        sanitize_relative(raw)


def test_strip_prefix() -> None:
    assert strip_prefix("wildcams/data/x.mp4", "wildcams/") == "data/x.mp4"
    assert strip_prefix("other/data/x.mp4", "wildcams/") == "other/data/x.mp4"
    assert strip_prefix("data/x.mp4", "") == "data/x.mp4"


def test_remote_path_with_and_without_root() -> None:
    rel = PurePosixPath("data/x.mp4")
    assert remote_path(rel, "") == "data/x.mp4"
    assert remote_path(rel, "/Data_backup/") == "Data_backup/data/x.mp4"
    with pytest.raises(UnsafePathError):
        remote_path(rel, "../elsewhere")


@pytest.mark.parametrize(
    "bad", ["", "../x", "20261112T120000Z-abc", "x" * 23, "20261112T120000Z-ABCDEF"]
)
def test_job_id_validation(bad: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_job_id(bad)
    assert validate_job_id(JOB) == JOB


def test_local_paths_stay_inside_job_dir(settings: Settings) -> None:
    p = local_media_path(settings, JOB, PurePosixPath("data/a b/c.mp4"))
    assert p.is_relative_to(job_media_dir(settings, JOB).resolve())
    assert p.name == "c.mp4"


def test_local_path_symlink_escape_is_blocked(settings: Settings, tmp_path: object) -> None:
    job_dir = job_media_dir(settings, JOB)
    job_dir.mkdir(parents=True)
    (job_dir / "evil").symlink_to("/tmp")
    with pytest.raises(UnsafePathError):
        local_media_path(settings, JOB, PurePosixPath("evil/x.mp4"))


def test_ls_url_is_relative_to_document_root_and_encoded(settings: Settings) -> None:
    url = ls_local_files_url(settings, JOB, PurePosixPath("data/Høst #1/a&b.mp4"))
    assert url == f"/data/local-files/?d=bridge/{JOB}/data/H%C3%B8st%20%231/a%26b.mp4"
