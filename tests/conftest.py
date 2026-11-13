"""Shared fixtures: isolated Settings, an in-process fake ERDA SFTP server, a fake Label Studio."""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncssh
import pytest

from wildcam_bridge.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parent.parent
SHARE = "TestShareLink123"
SAMPLE_PREFIX = "data/testsite/site_0001/site_0001_241217_250416/"
SAMPLE_FILES = {
    SAMPLE_PREFIX + "img_0001.mp4": b"\x00\x00\x00\x18ftypmp42" + b"A" * 3000,
    SAMPLE_PREFIX + "img_0002.mp4": b"\x00\x00\x00\x18ftypmp42" + b"B" * 5000,
    SAMPLE_PREFIX + "img_0003.mp4": b"\x00\x00\x00\x18ftypmp42" + b"C" * 7000,
    SAMPLE_PREFIX + "sub dir/img 0004.mp4": b"\x00\x00\x00\x18ftypmp42" + b"D" * 1000,
}


# ---------------------------------------------------------------------------- fake ERDA
class _SSHServer(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        # ERDA sharelinks: username == password == sharelink id
        return username == SHARE and password == SHARE


class FakeErda:
    """asyncssh SFTP server on 127.0.0.1 (random port) chrooted to a temp folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.fail_once: set[str] = set()  # file names whose first open() fails
        self._failed: set[str] = set()
        self.opens: list[str] = []
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.port = 0

    def add(self, rel: str, content: bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def known_hosts_line(self) -> str:
        pub = self.host_key.export_public_key().decode().strip()
        return f"[127.0.0.1]:{self.port} {pub}\n"

    def start(self) -> FakeErda:
        self._thread.start()
        fake = self

        class _SFTP(asyncssh.SFTPServer):
            def __init__(self, chan: Any) -> None:
                super().__init__(chan, chroot=str(fake.root).encode())

            def open(self, path: bytes, pflags: int, attrs: Any) -> Any:
                name = path.decode(errors="replace").rsplit("/", 1)[-1]
                fake.opens.append(name)
                if name in fake.fail_once and name not in fake._failed:
                    fake._failed.add(name)
                    raise asyncssh.SFTPFailure("simulated transient failure")
                return super().open(path, pflags, attrs)

        async def _listen() -> Any:
            return await asyncssh.listen(
                "127.0.0.1",
                0,
                server_factory=_SSHServer,
                server_host_keys=[self.host_key],
                sftp_factory=_SFTP,
            )

        self._acceptor = asyncio.run_coroutine_threadsafe(_listen(), self.loop).result(10)
        self.port = self._acceptor.get_port()
        return self

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self._acceptor.close)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@pytest.fixture
def fake_erda(tmp_path: Path) -> Iterator[FakeErda]:
    root = tmp_path / "erda"
    root.mkdir()
    server = FakeErda(root).start()
    for rel, content in SAMPLE_FILES.items():
        server.add(rel, content)
    yield server
    server.stop()


# ---------------------------------------------------------------------------- settings
def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    media = tmp_path / "labelstudio-data"
    media.mkdir(exist_ok=True)
    values: dict[str, Any] = {
        "bridge_state_dir": tmp_path / "var",
        "bridge_api_key_hashes": "",
        "bridge_require_https": True,
        "erda_host": "127.0.0.1",
        "erda_port": 1,
        "erda_sharelink": SHARE,
        "erda_known_hosts": tmp_path / "known_hosts",
        "erda_strip_prefix": "wildcams/",
        "erda_retries": 2,
        "ls_data_root": media,
        "disk_reserve_gb": 0,
        "ls_url": "http://ls.invalid",
        "ls_public_url": "https://ls.example.org",
        "ls_api_token": "legacy-token-123",
        "ls_label_config": REPO / "config" / "label_config.xml",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def erda_settings(tmp_path: Path, fake_erda: FakeErda) -> Settings:
    (tmp_path / "known_hosts").write_text(fake_erda.known_hosts_line())
    return make_settings(tmp_path, erda_port=fake_erda.port)


@pytest.fixture
def sample_csv() -> bytes:
    return (FIXTURES / "sample_query.csv").read_bytes()


# ---------------------------------------------------------------------------- fake Label Studio
class FakeLabelStudio:
    """In-memory stand-in for LabelStudioClient (same public methods)."""

    _ids = itertools.count(1)

    def __init__(self) -> None:
        self.projects: dict[int, dict[str, Any]] = {}
        self.storages: dict[int, list[dict[str, Any]]] = {}
        self.tasks: dict[int, list[dict[str, Any]]] = {}

    def __enter__(self) -> FakeLabelStudio:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def whoami(self) -> dict[str, Any]:
        return {"id": 1}

    def create_project(
        self, title: str, label_config: str, description: str = ""
    ) -> dict[str, Any]:
        pid = next(self._ids)
        self.projects[pid] = {
            "id": pid,
            "title": title,
            "label_config": label_config,
            "description": description,
        }
        self.tasks[pid] = []
        return self.projects[pid]

    def get_project(self, project_id: int) -> dict[str, Any]:
        from wildcam_bridge.labelstudio import LabelStudioError

        if project_id not in self.projects:
            raise LabelStudioError("not found", 404)
        tasks = self.tasks[project_id]
        done = sum(1 for t in tasks if t.get("annotations"))
        return {
            **self.projects[project_id],
            "task_number": len(tasks),
            "finished_task_number": done,
            "num_tasks_with_annotations": done,
            "total_annotations_number": sum(len(t.get("annotations", [])) for t in tasks),
        }

    def update_project(self, project_id: int, **fields: Any) -> dict[str, Any]:
        self.projects[project_id].update(fields)
        return self.projects[project_id]

    def delete_project(self, project_id: int) -> None:
        self.get_project(project_id)  # 404 if unknown
        del self.projects[project_id], self.tasks[project_id]
        self.storages.pop(project_id, None)

    def list_local_storages(self, project_id: int) -> list[dict[str, Any]]:
        return self.storages.get(project_id, [])

    def create_local_storage(self, project_id: int, path: str, title: str) -> dict[str, Any]:
        st = {"project": project_id, "path": path, "title": title}
        self.storages.setdefault(project_id, []).append(st)
        return st

    def import_tasks(self, project_id: int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        for t in tasks:
            self.tasks[project_id].append({"id": next(self._ids), **t, "annotations": []})
        return {"task_count": len(tasks)}

    def export_tasks(
        self, project_id: int, *, download_all_tasks: bool = True
    ) -> list[dict[str, Any]]:
        return self.tasks[project_id]

    def project_progress(self, project_id: int) -> dict[str, Any]:
        from wildcam_bridge.labelstudio import LabelStudioClient

        return LabelStudioClient.project_progress(self, project_id)  # type: ignore[arg-type]


@pytest.fixture
def fake_ls() -> FakeLabelStudio:
    return FakeLabelStudio()
