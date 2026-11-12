"""API tests: auth, HTTPS, validation, and the whole pipeline (fake ERDA + fake Label Studio)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import SAMPLE_FILES, FakeErda, FakeLabelStudio, make_settings
from wildcam_bridge.api import create_app
from wildcam_bridge.auth import generate_api_key, hash_api_key
from wildcam_bridge.settings import Settings

KEY = generate_api_key()
HDR = {"Authorization": f"Bearer {KEY}", "X-Forwarded-Proto": "https"}


@pytest.fixture
def app_settings(tmp_path: Path, fake_erda: FakeErda) -> Settings:
    (tmp_path / "known_hosts").write_text(fake_erda.known_hosts_line())
    return make_settings(
        tmp_path, erda_port=fake_erda.port, bridge_api_key_hashes=hash_api_key(KEY)
    )


@pytest.fixture
def client(app_settings: Settings, fake_ls: FakeLabelStudio) -> Iterator[TestClient]:
    app = create_app(app_settings, ls_factory=lambda s: fake_ls)  # type: ignore[arg-type,return-value]
    with TestClient(app) as c:
        c.app_state = app.state  # type: ignore[attr-defined]
        yield c


def _submit(client: TestClient, csv: bytes, **form: Any) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/jobs",
        headers=HDR,
        files={"file": ("q.csv", csv, "text/csv")},
        data={k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()},
    )
    assert resp.status_code == 202, resp.text
    client.app_state.runner.join_idle()  # type: ignore[attr-defined]
    job = client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=HDR).json()
    assert isinstance(job, dict)
    return job


def test_healthz_is_open(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_https_required(client: TestClient) -> None:
    resp = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {KEY}"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "HTTPS required"


@pytest.mark.parametrize("auth", [None, "Bearer wrong", f"Basic {KEY}"])
def test_api_key_required(client: TestClient, auth: str | None) -> None:
    headers = {"X-Forwarded-Proto": "https"}
    if auth:
        headers["Authorization"] = auth
    resp = client.get("/api/v1/jobs", headers=headers)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_security_headers(client: TestClient) -> None:
    resp = client.get("/api/v1/jobs", headers=HDR)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"


def test_invalid_csv_is_rejected_immediately(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/jobs",
        headers=HDR,
        files={"file": ("q.csv", b"media_id,file\n1,../x.mp4\n", "text/csv")},
    )
    assert resp.status_code == 400
    assert "unsafe file path" in resp.json()["detail"]


def test_csv_too_large(tmp_path: Path, fake_ls: FakeLabelStudio) -> None:
    s = make_settings(tmp_path, bridge_api_key_hashes=hash_api_key(KEY), bridge_max_csv_bytes=10)
    with TestClient(create_app(s, start_worker=False)) as c:
        resp = c.post(
            "/api/v1/jobs",
            headers=HDR,
            files={"file": ("q.csv", b"media_id,file\nm,a.mp4\n", "text/csv")},
        )
    assert resp.status_code == 413


def test_dry_run_job(client: TestClient, sample_csv: bytes, app_settings: Settings) -> None:
    job = _submit(client, sample_csv, dry_run=True)
    assert job["status"] == "dry_run_ok", job
    assert job["report"]["dry_run"]["n_found"] == 4
    assert job["project_id"] is None
    assert not (app_settings.jobs_media_root / job["id"]).exists()  # nothing downloaded


def test_full_job_then_results(
    client: TestClient, sample_csv: bytes, fake_ls: FakeLabelStudio, app_settings: Settings
) -> None:
    job = _submit(client, sample_csv, title="Hoestemark test")
    assert job["status"] == "ready", job
    pid = job["project_id"]
    assert job["project_url"] == f"https://ls.example.org/projects/{pid}/data"
    assert fake_ls.projects[pid]["title"] == "Hoestemark test"
    assert fake_ls.projects[pid]["show_collab_predictions"] is True
    assert len(fake_ls.tasks[pid]) == 4
    media_dir = app_settings.jobs_media_root / job["id"]
    assert fake_ls.storages[pid][0]["path"] == str(media_dir.resolve())
    assert (media_dir / "tasks.json").is_file()
    for rel, content in SAMPLE_FILES.items():
        assert (media_dir / rel).read_bytes() == content
    assert any("unicorn" in w for w in job["warnings"])

    prog = client.get(f"/api/v1/jobs/{job['id']}/progress", headers=HDR).json()
    assert (prog["total_tasks"], prog["finished_tasks"], prog["complete"]) == (4, 0, False)

    # an annotator labels every task
    for t in fake_ls.tasks[pid]:
        t["annotations"] = [
            {
                "id": 1,
                "completed_by": 2,
                "result": [
                    {
                        "from_name": "species_label",
                        "type": "choices",
                        "value": {"choices": ["fox"]},
                    },
                    {"from_name": "count", "type": "number", "value": {"number": 3}},
                ],
            }
        ]
    prog = client.get(f"/api/v1/jobs/{job['id']}/progress", headers=HDR).json()
    assert prog["complete"] is True

    res = client.get(f"/api/v1/jobs/{job['id']}/results", headers=HDR).json()
    assert len(res["rows"]) == 4
    assert res["rows"][0]["species_label"] == "fox"
    assert res["rows"][0]["media_id"] == "m-0001"
    csv_resp = client.get(f"/api/v1/jobs/{job['id']}/results?format=csv", headers=HDR)
    assert csv_resp.status_code == 200, csv_resp.text
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("project_id,task_id,media_id")
    assert len(csv_resp.text.splitlines()) == 5

    # the generic per-project endpoint returns the same
    res2 = client.get(f"/api/v1/projects/{pid}/results", headers=HDR).json()
    assert res2["rows"] == res["rows"]
    assert client.post(f"/api/v1/jobs/{job['id']}/retry", headers=HDR).status_code == 409


def test_missing_files_fail_unless_allowed(
    client: TestClient, sample_csv: bytes, fake_erda: FakeErda
) -> None:
    (fake_erda.root / next(iter(SAMPLE_FILES))).unlink()
    job = _submit(client, sample_csv)
    assert job["status"] == "failed"
    assert "1 of 4 file(s) not found" in job["error"]
    assert client.get(f"/api/v1/jobs/{job['id']}/results", headers=HDR).status_code == 409

    job = _submit(client, sample_csv, allow_missing=True)
    assert job["status"] == "ready", job
    assert job["progress"]["tasks"] == 3


def test_retry_after_failure(client: TestClient, sample_csv: bytes, fake_erda: FakeErda) -> None:
    victim = next(iter(SAMPLE_FILES))
    content = (fake_erda.root / victim).read_bytes()
    (fake_erda.root / victim).unlink()
    job = _submit(client, sample_csv)
    assert job["status"] == "failed"
    fake_erda.add(victim, content)  # file appears on ERDA
    resp = client.post(f"/api/v1/jobs/{job['id']}/retry", headers=HDR)
    assert resp.status_code == 202
    client.app_state.runner.join_idle()  # type: ignore[attr-defined]
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=HDR).json()["status"] == "ready"


def test_unknown_job_and_project(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/20261112T120000Z-abcdef", headers=HDR).status_code == 404
    assert client.get("/api/v1/projects/999/results", headers=HDR).status_code == 404


def test_list_jobs(client: TestClient, sample_csv: bytes) -> None:
    _submit(client, sample_csv, dry_run=True)
    jobs = client.get("/api/v1/jobs", headers=HDR).json()
    assert len(jobs) == 1 and jobs[0]["status"] == "dry_run_ok"


def test_docs_served_under_root_path(app_settings: Settings, fake_ls: FakeLabelStudio) -> None:
    app = create_app(app_settings, start_worker=False)
    with TestClient(app, root_path="/bridge") as c:
        spec = c.get("/openapi.json", headers=HDR).json()
    assert spec["servers"] == [{"url": "/bridge"}]
    assert "/api/v1/jobs" in spec["paths"]
    params = spec["paths"]["/api/v1/jobs/{job_id}/results"]["get"]["parameters"]
    assert "format" in [p["name"] for p in params]


def test_bad_label_studio_token_fails_before_download(
    app_settings: Settings, sample_csv: bytes
) -> None:
    from wildcam_bridge.labelstudio import LabelStudioClient

    def factory(s: Settings) -> LabelStudioClient:
        return LabelStudioClient("http://127.0.0.1:9", "")  # raises: token not set

    app = create_app(app_settings, ls_factory=factory)
    with TestClient(app) as c:
        c.app_state = app.state  # type: ignore[attr-defined]
        job = _submit(c, sample_csv)
    assert job["status"] == "failed"
    assert job["error"].startswith("Label Studio check failed: LS_API_TOKEN is not set")
    assert job["report"] == {}  # stopped before the ERDA dry run
    assert not (app_settings.jobs_media_root / job["id"]).exists()
