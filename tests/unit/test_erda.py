"""Transfer tests against a real (in-process) SFTP server with pinned host key."""

import asyncio
from pathlib import Path

import asyncssh
import pytest

from tests.conftest import SAMPLE_FILES, SHARE, FakeErda, make_settings
from wildcam_bridge import erda
from wildcam_bridge.csv_ingest import parse_csv
from wildcam_bridge.settings import Settings

JOB = "20261112T120000Z-abcdef"


def _files(settings: Settings, sample_csv: bytes) -> list[erda.RemoteFile]:
    items = parse_csv(sample_csv, strip=settings.erda_strip_prefix).items
    return erda.plan_files(settings, JOB, items)


def test_dry_run_ok(erda_settings: Settings, sample_csv: bytes) -> None:
    files = _files(erda_settings, sample_csv)
    report = asyncio.run(erda.dry_run(erda_settings, files))
    assert report.ok, report.errors
    assert (report.n_files, report.n_found, report.missing) == (4, 4, [])
    assert report.total_bytes == sum(len(c) for c in SAMPLE_FILES.values())
    assert report.bytes_to_download == report.total_bytes
    assert report.free_bytes > 0
    # nothing was downloaded
    assert not any(f.local.exists() for f in files)


def test_dry_run_reports_missing(
    erda_settings: Settings, sample_csv: bytes, fake_erda: FakeErda
) -> None:
    victim = next(iter(SAMPLE_FILES))
    (fake_erda.root / victim).unlink()
    report = asyncio.run(erda.dry_run(erda_settings, _files(erda_settings, sample_csv)))
    assert report.connected
    assert report.n_found == 3
    assert report.missing == [victim]
    assert report.to_dict()["n_missing"] == 1


def test_dry_run_not_enough_space(tmp_path: Path, fake_erda: FakeErda, sample_csv: bytes) -> None:
    (tmp_path / "known_hosts").write_text(fake_erda.known_hosts_line())
    s = make_settings(tmp_path, erda_port=fake_erda.port, disk_reserve_gb=1e9)  # 1 EB reserve
    report = asyncio.run(erda.dry_run(s, _files(s, sample_csv)))
    assert report.connected and not report.enough_space and not report.ok


def test_wrong_host_key_is_refused(tmp_path: Path, fake_erda: FakeErda, sample_csv: bytes) -> None:
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    (tmp_path / "known_hosts").write_text(f"[127.0.0.1]:{fake_erda.port} {other}\n")
    s = make_settings(tmp_path, erda_port=fake_erda.port)
    report = asyncio.run(erda.dry_run(s, _files(s, sample_csv)))
    assert not report.connected
    assert "host key does not match" in report.errors[0]


def test_wrong_sharelink_is_refused(tmp_path: Path, fake_erda: FakeErda, sample_csv: bytes) -> None:
    (tmp_path / "known_hosts").write_text(fake_erda.known_hosts_line())
    s = make_settings(tmp_path, erda_port=fake_erda.port, erda_sharelink=SHARE + "x")
    report = asyncio.run(erda.dry_run(s, _files(s, sample_csv)))
    assert not report.connected
    assert "cannot connect" in report.errors[0]


@pytest.mark.parametrize("missing", ["sharelink", "known_hosts"])
def test_missing_configuration(tmp_path: Path, missing: str) -> None:
    s = make_settings(tmp_path, erda_sharelink="" if missing == "sharelink" else SHARE)
    if missing == "known_hosts":
        s = s.model_copy(update={"erda_known_hosts": tmp_path / "absent"})
    with pytest.raises(
        erda.ErdaError, match="ERDA_SHARELINK" if missing == "sharelink" else "host key file"
    ):
        asyncio.run(erda.check_connection(s))


def test_download_skip_resume_and_verify(erda_settings: Settings, sample_csv: bytes) -> None:
    files = _files(erda_settings, sample_csv)
    asyncio.run(erda.dry_run(erda_settings, files))
    # a complete file from an earlier run, and a truncated one from a crash
    files[0].local.parent.mkdir(parents=True)
    files[0].local.write_bytes(SAMPLE_FILES[files[0].remote])
    files[1].local.write_bytes(b"truncated")
    progress: list[dict[str, int]] = []

    report = asyncio.run(erda.download(erda_settings, files, progress.append))

    assert report.failed == {}
    assert (report.skipped, report.downloaded) == (1, 3)
    for f in files:
        assert f.local.read_bytes() == SAMPLE_FILES[f.remote]
        assert not f.local.with_name(f.local.name + ".part").exists()
    assert progress[-1]["files_done"] == progress[-1]["files_total"] == 4
    # second run downloads nothing
    again = asyncio.run(erda.download(erda_settings, files))
    assert (again.skipped, again.downloaded) == (4, 0)


def test_transient_failure_is_retried(
    erda_settings: Settings, sample_csv: bytes, fake_erda: FakeErda, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(erda.asyncio, "sleep", _no_sleep)
    files = _files(erda_settings, sample_csv)
    asyncio.run(erda.dry_run(erda_settings, files))
    fake_erda.fail_once = {"img_0002.mp4"}
    report = asyncio.run(erda.download(erda_settings, files))
    assert report.failed == {}
    assert report.downloaded == 4
    assert fake_erda.opens.count("img_0002.mp4") == 2


def test_permanent_failure_is_reported(
    erda_settings: Settings, sample_csv: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(erda.asyncio, "sleep", _no_sleep)
    files = _files(erda_settings, sample_csv)
    asyncio.run(erda.dry_run(erda_settings, files))
    files[2].size = 1  # ERDA "changed" the file: size check must fail on every attempt
    report = asyncio.run(erda.download(erda_settings, files))
    assert list(report.failed) == [files[2].remote]
    assert "size mismatch" in report.failed[files[2].remote]
    assert not files[2].local.exists()


async def _no_sleep(_: float) -> None:
    return None
