"""Transfer videos from AU ERDA over SFTP (SSH-encrypted).

Merges the two historical scripts into one robust routine:

* ``rsync.py``  - concurrent ``sftp.get`` downloads with a semaphore, skipping existing files;
* ``miss_files.py`` - a second pass that re-downloads whatever is still missing.

Improvements over the originals:

* the ERDA host key is PINNED (``known_hosts=None`` in the old scripts disabled MITM protection);
* credentials come from ``.env`` (the sharelink is username AND password);
* a DRY RUN stats every remote file and checks free disk space BEFORE downloading;
* "existing" means same size as on ERDA (a truncated file from a crash is re-downloaded);
* downloads go to ``<name>.part`` and are renamed only after the size check (atomic);
* the retry pass reconnects (a dropped connection is the usual reason for misses).

ERDA access facts (verified 2026-11-12): host io.erda.au.dk, port 2222, sharelink root ==
the ERDA ``wildcams`` folder (contains data/, Data_backup/, Test_data/, models/, ...). ERDA
does not support ``statvfs``, so remote free space cannot be (and need not be) checked.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import stat
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import asyncssh

from wildcam_bridge.csv_ingest import MediaItem
from wildcam_bridge.paths import local_media_path, remote_path
from wildcam_bridge.settings import Settings

log = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]


class ErdaError(RuntimeError):
    pass


@dataclass
class RemoteFile:
    media_id: str
    remote: str
    local: Path
    size: int | None = None  # None = not found on ERDA

    @property
    def found(self) -> bool:
        return self.size is not None

    def is_complete_locally(self) -> bool:
        return self.found and self.local.is_file() and self.local.stat().st_size == self.size


@dataclass
class DryRunReport:
    connected: bool = False
    host: str = ""
    n_files: int = 0
    n_found: int = 0
    missing: list[str] = field(default_factory=list)
    total_bytes: int = 0
    already_local_bytes: int = 0
    bytes_to_download: int = 0
    free_bytes: int = 0
    reserve_bytes: int = 0
    enough_space: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.connected and self.enough_space and not self.errors

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok"] = self.ok
        d["missing_sample"] = d.pop("missing")[:50]
        d["n_missing"] = len(self.missing)
        return d


@dataclass
class DownloadReport:
    downloaded: int = 0
    skipped: int = 0
    bytes_downloaded: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # remote -> error

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["n_failed"] = len(self.failed)
        d["failed"] = dict(list(self.failed.items())[:50])
        return d


def plan_files(settings: Settings, job_id: str, items: list[MediaItem]) -> list[RemoteFile]:
    return [
        RemoteFile(
            media_id=item.media_id,
            remote=remote_path(item.rel_path, settings.erda_remote_root),
            local=local_media_path(settings, job_id, item.rel_path),
        )
        for item in items
    ]


@contextlib.asynccontextmanager
async def sftp_session(settings: Settings) -> AsyncIterator[asyncssh.SFTPClient]:
    share = settings.erda_sharelink.get_secret_value()
    if not share:
        raise ErdaError("ERDA_SHARELINK is not set (.env)")
    if not settings.erda_known_hosts.is_file():
        raise ErdaError(f"pinned ERDA host key file not found: {settings.erda_known_hosts}")
    try:
        conn = await asyncssh.connect(
            settings.erda_host,
            port=settings.erda_port,
            username=share,
            password=share,
            known_hosts=str(settings.erda_known_hosts),
            client_keys=None,
            agent_path=None,
            preferred_auth=("password", "keyboard-interactive"),
            connect_timeout=settings.erda_connect_timeout,
        )
    except asyncssh.HostKeyNotVerifiable as exc:
        raise ErdaError(
            "ERDA host key does not match the pinned key "
            f"({settings.erda_known_hosts}); refusing to connect"
        ) from exc
    except (OSError, asyncssh.Error) as exc:
        raise ErdaError(
            f"cannot connect to {settings.erda_host}:{settings.erda_port}: {exc}"
        ) from exc
    try:
        async with conn, conn.start_sftp_client() as sftp:
            yield sftp
    finally:
        conn.close()


async def _stat_all(
    sftp: asyncssh.SFTPClient, files: list[RemoteFile], concurrency: int
) -> list[str]:
    sem = asyncio.Semaphore(concurrency)
    errors: list[str] = []

    async def one(f: RemoteFile) -> None:
        async with sem:
            try:
                attrs = await sftp.stat(f.remote)
            except asyncssh.SFTPNoSuchFile:
                f.size = None
                return
            except asyncssh.SFTPError as exc:
                errors.append(f"{f.remote}: {exc}")
                return
            if attrs.permissions is not None and not stat.S_ISREG(attrs.permissions):
                errors.append(f"{f.remote}: not a regular file")
                return
            f.size = int(attrs.size or 0)

    await asyncio.gather(*(one(f) for f in files))
    return errors


def disk_free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


async def dry_run(settings: Settings, files: list[RemoteFile]) -> DryRunReport:
    """Connect, stat every remote file, and check local free space. Downloads nothing."""
    report = DryRunReport(
        host=f"{settings.erda_host}:{settings.erda_port}",
        n_files=len(files),
        reserve_bytes=settings.disk_reserve_bytes,
    )
    try:
        async with sftp_session(settings) as sftp:
            report.connected = True
            report.errors.extend(await _stat_all(sftp, files, settings.erda_concurrency))
    except ErdaError as exc:
        report.errors.append(str(exc))
        return report

    found = [f for f in files if f.found]
    report.n_found = len(found)
    report.missing = [f.remote for f in files if not f.found]
    report.total_bytes = sum(f.size or 0 for f in found)
    report.already_local_bytes = sum(f.size or 0 for f in found if f.is_complete_locally())
    report.bytes_to_download = report.total_bytes - report.already_local_bytes
    report.free_bytes = disk_free_bytes(settings.jobs_media_root)
    report.enough_space = report.free_bytes - report.bytes_to_download >= report.reserve_bytes
    return report


async def _download_pass(
    settings: Settings,
    files: list[RemoteFile],
    report: DownloadReport,
    concurrency: int,
    on_progress: ProgressCallback | None,
    total: int,
) -> list[RemoteFile]:
    """Download ``files``; return the ones that failed."""
    sem = asyncio.Semaphore(concurrency)
    failed: list[RemoteFile] = []

    async def one(sftp: asyncssh.SFTPClient, f: RemoteFile) -> None:
        async with sem:
            part = f.local.with_name(f.local.name + ".part")
            try:
                f.local.parent.mkdir(parents=True, exist_ok=True)
                await sftp.get(f.remote, str(part), preserve=True)
                got = part.stat().st_size
                if got != f.size:
                    raise ErdaError(f"size mismatch: got {got} bytes, expected {f.size}")
                part.replace(f.local)
            except (OSError, asyncssh.Error, ErdaError) as exc:
                part.unlink(missing_ok=True)
                report.failed[f.remote] = str(exc) or type(exc).__name__
                failed.append(f)
                log.warning("download failed %s: %s", f.remote, exc)
                return
            report.failed.pop(f.remote, None)
            report.downloaded += 1
            report.bytes_downloaded += f.size or 0
            if on_progress:
                on_progress(
                    {
                        "files_done": report.downloaded + report.skipped,
                        "files_total": total,
                        "bytes_downloaded": report.bytes_downloaded,
                    }
                )

    try:
        async with sftp_session(settings) as sftp:
            await asyncio.gather(*(one(sftp, f) for f in files))
    except ErdaError as exc:  # connection-level failure: everything not done is failed
        for f in files:
            if not f.is_complete_locally() and f not in failed:
                report.failed[f.remote] = str(exc)
                failed.append(f)
    return failed


async def download(
    settings: Settings,
    files: list[RemoteFile],
    on_progress: ProgressCallback | None = None,
) -> DownloadReport:
    """Download every found file that is not already complete locally, with retries."""
    report = DownloadReport()
    todo: list[RemoteFile] = []
    for f in files:
        if not f.found:
            continue
        if f.is_complete_locally():
            report.skipped += 1
        else:
            todo.append(f)
    total = report.skipped + len(todo)
    if on_progress:
        on_progress({"files_done": report.skipped, "files_total": total, "bytes_downloaded": 0})

    # Pass 1: concurrent (rsync.py). Later passes: fresh connection, gentler (miss_files.py).
    concurrency = settings.erda_concurrency
    for attempt in range(settings.erda_retries + 1):
        if not todo:
            break
        if attempt:
            delay = min(2**attempt, 30)
            log.info("retry pass %d for %d file(s) in %ds", attempt, len(todo), delay)
            await asyncio.sleep(delay)
            concurrency = max(1, concurrency // 2)
        todo = await _download_pass(settings, todo, report, concurrency, on_progress, total)
    return report


async def check_connection(settings: Settings) -> None:
    """Raise ErdaError unless we can log in and list the share root."""
    async with sftp_session(settings) as sftp:
        await sftp.listdir(".")
