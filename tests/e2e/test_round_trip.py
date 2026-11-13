"""END-TO-END: CSV -> API -> SFTP -> REAL Label Studio project -> annotation -> results.

Starts a throw-away Label Studio (separate port, data dir and DB; production is untouched)
from the server's existing installation, plus the in-process fake ERDA SFTP server.

    make e2e                       # or: uv run pytest -m e2e
    E2E_LS_BIN=/path/to/label-studio uv run pytest -m e2e

To iterate quickly against an already running throw-away instance, set
E2E_LS_URL, E2E_LS_TOKEN (legacy token), E2E_LS_MEDIA_ROOT (its LOCAL_FILES_DOCUMENT_ROOT)
and, if a personal access token was already minted for that user, E2E_LS_PAT.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import SAMPLE_FILES, FakeErda, make_settings
from wildcam_bridge.api import create_app
from wildcam_bridge.auth import generate_api_key, hash_api_key
from wildcam_bridge.labelstudio import LabelStudioClient, LabelStudioError

pytestmark = pytest.mark.e2e

LS_BIN = Path(os.environ.get("E2E_LS_BIN", "~/labelstudio/.venv/bin/label-studio")).expanduser()
KEY = generate_api_key()
HDR = {"Authorization": f"Bearer {KEY}", "X-Forwarded-Proto": "https"}


@dataclass
class LiveLS:
    url: str
    legacy_token: str
    pat: str
    media_root: Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _wait_healthy(url: str, proc: subprocess.Popen[bytes] | None, log: Path | None) -> None:
    deadline = time.monotonic() + 400
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            tail = log.read_text()[-3000:] if log else ""
            raise RuntimeError(f"Label Studio exited early:\n{tail}")
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise TimeoutError("Label Studio did not become healthy")


def _mint_pat(url: str, legacy: str) -> str:
    resp = httpx.post(f"{url}/api/token/", headers={"Authorization": f"Token {legacy}"}, timeout=30)
    resp.raise_for_status()
    token: str = resp.json()["token"]
    return token


@pytest.fixture(scope="session")
def live_ls(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveLS]:
    if url := os.environ.get("E2E_LS_URL"):
        legacy = os.environ["E2E_LS_TOKEN"]
        # LS allows ONE personal access token per user: pass it if it was minted before.
        pat = os.environ.get("E2E_LS_PAT") or _mint_pat(url, legacy)
        yield LiveLS(url, legacy, pat, Path(os.environ["E2E_LS_MEDIA_ROOT"]))
        return
    if not LS_BIN.exists():
        pytest.skip(f"Label Studio not found at {LS_BIN} (set E2E_LS_BIN)")
    base = tmp_path_factory.mktemp("label-studio")
    data, media = base / "data", base / "media"
    data.mkdir()
    media.mkdir()
    port, legacy = _free_port(), secrets.token_hex(20)
    env = {
        **os.environ,
        "LABEL_STUDIO_BASE_DATA_DIR": str(data),
        "LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED": "true",
        "LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT": str(media),
    }
    log = base / "label-studio.log"
    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [
                str(LS_BIN),
                "start",
                "--no-browser",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data-dir",
                str(data),
                "--username",
                "e2e@example.org",
                "--password",
                secrets.token_urlsafe(16),
                "--user-token",
                legacy,
                "--enable-legacy-api-token",
            ],
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=base,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(url, proc, log)
        yield LiveLS(url, legacy, _mint_pat(url, legacy), media)
    finally:
        proc.terminate()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()


def _annotation(species: str, count: int, notes: str) -> dict[str, Any]:
    def ch(name: str, value: str) -> dict[str, Any]:
        return {
            "from_name": name,
            "to_name": "video",
            "type": "choices",
            "value": {"choices": [value]},
        }

    return {
        "lead_time": 12.5,
        "result": [
            ch("species_label", species),
            {
                "from_name": "count",
                "to_name": "video",
                "type": "number",
                "value": {"number": count},
            },
            ch("certainty", "Moderate"),
            ch("day_night", "Night"),
            {
                "from_name": "notes",
                "to_name": "video",
                "type": "textarea",
                "value": {"text": [notes]},
            },
        ],
    }


def test_full_round_trip(
    live_ls: LiveLS, tmp_path: Path, fake_erda: FakeErda, sample_csv: bytes
) -> None:
    (tmp_path / "known_hosts").write_text(fake_erda.known_hosts_line())
    settings = make_settings(
        tmp_path,
        erda_port=fake_erda.port,
        ls_data_root=live_ls.media_root,
        ls_url=live_ls.url,
        ls_api_token=live_ls.pat,  # the production auth path (Personal Access Token)
        bridge_api_key_hashes=hash_api_key(KEY),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # ---------------------------------------------------------------- inbound
        dry = client.post(
            "/api/v1/jobs",
            headers=HDR,
            data={"dry_run": "true"},
            files={"file": ("q.csv", sample_csv, "text/csv")},
        )
        assert dry.status_code == 202
        resp = client.post(
            "/api/v1/jobs",
            headers=HDR,
            data={"title": "[E2E] bridge test"},
            files={"file": ("q.csv", sample_csv, "text/csv")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["id"]
        app.state.runner.join_idle()

        assert (
            client.get(f"/api/v1/jobs/{dry.json()['id']}", headers=HDR).json()["status"]
            == "dry_run_ok"
        )
        job = client.get(f"/api/v1/jobs/{job_id}", headers=HDR).json()
        assert job["status"] == "ready", job
        assert not any("pre-annotations" in w for w in job["warnings"]), job["warnings"]
        pid = job["project_id"]

        # ---------------------------------------------------------------- Label Studio side
        with LabelStudioClient(live_ls.url, live_ls.legacy_token) as ls:
            project = ls.get_project(pid)
            assert project["title"] == "[E2E] bridge test"
            assert project["task_number"] == 4
            assert 'name="species_label"' in project["label_config"]
            assert project["show_collab_predictions"] is True
            assert project["reveal_preannotations_interactively"] is False
            assert ls.list_local_storages(pid)[0]["path"].endswith(job_id)

            tasks = ls.export_tasks(pid)
            by_media = {t["data"]["media_id"]: t for t in tasks}

            # (the 1.23 export lists prediction ids only -> read the full task objects)
            def pred(media_id: str) -> list[Any]:
                task = ls.request("GET", f"/api/tasks/{by_media[media_id]['id']}").json()
                return [p["result"][0]["value"]["choices"] for p in task["predictions"]]

            assert pred("m-0001") == [["red deer"]]
            assert pred("m-0002") == [["human"]]  # most confident of the 2 CSV rows
            assert pred("m-0003") == []  # "unicorn" is not a UI choice
            assert project["model_version"] == "dfbsmdr-01.04.00"

            # Videos are streamed by Label Studio from the job folder (needs the local storage).
            for t in tasks:
                media = ls.request("GET", t["data"]["video"])
                rel = t["data"]["file"].removeprefix("wildcams/")
                assert media.content == SAMPLE_FILES[rel]

            progress = client.get(f"/api/v1/jobs/{job_id}/progress", headers=HDR).json()
            assert (progress["total_tasks"], progress["complete"]) == (4, False)

            # ---------------------------------------------------------------- the human annotator
            for i, t in enumerate(tasks):
                ls.request(
                    "POST",
                    f"/api/tasks/{t['id']}/annotations",
                    json=_annotation("fox" if i else "red deer", i + 1, f"note {i}"),
                )

        # ---------------------------------------------------------------- outbound
        progress = client.get(f"/api/v1/jobs/{job_id}/progress", headers=HDR).json()
        assert progress["complete"] is True
        results = client.get(f"/api/v1/jobs/{job_id}/results", headers=HDR).json()
        rows = {r["media_id"]: r for r in results["rows"]}
        assert len(rows) == 4
        assert rows["m-0001"]["species_label"] == "red deer"
        assert rows["m-0001"]["model_prediction"] == "red deer"
        assert rows["m-0002"]["species_label"] == "fox"
        assert rows["m-0002"]["certainty"] == "Moderate"
        assert rows["m-0002"]["day_night"] == "Night"
        assert {r["count"] for r in rows.values()} == {1, 2, 3, 4}
        assert all(r["annotator_id"] for r in rows.values())
        csv_text = client.get(f"/api/v1/jobs/{job_id}/results?format=csv", headers=HDR).text
        assert len(csv_text.strip().splitlines()) == 5

        # ---------------------------------------------------------------- protection
        assert (
            client.get(
                f"/api/v1/jobs/{job_id}/results", headers={"X-Forwarded-Proto": "https"}
            ).status_code
            == 401
        )
        assert (
            client.get(
                f"/api/v1/jobs/{job_id}/results", headers={"Authorization": HDR["Authorization"]}
            ).status_code
            == 403
        )

        # ---------------------------------------------------------------- deletion
        refused = client.delete(f"/api/v1/projects/{pid}", headers=HDR)
        assert refused.status_code == 409, refused.text  # 4 annotations: needs force
        deleted = client.delete(f"/api/v1/jobs/{job_id}?force=true", headers=HDR)
        assert deleted.status_code == 200, deleted.text
        summary = deleted.json()["report"]["deleted"]
        assert (summary["label_studio_project"], summary["annotations_deleted"]) == ("deleted", 4)
        with LabelStudioClient(live_ls.url, live_ls.legacy_token) as ls:
            with pytest.raises(LabelStudioError) as gone:
                ls.get_project(pid)
            assert gone.value.status_code == 404
        assert not (settings.jobs_media_root / job_id).exists()
        assert client.get(f"/api/v1/jobs/{job_id}/results", headers=HDR).status_code == 410
