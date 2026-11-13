import json
from pathlib import Path

import httpx
import pytest

from tests.conftest import FIXTURES, SHARE, FakeErda, FakeLabelStudio
from wildcam_bridge import cli, pipeline
from wildcam_bridge.client import BridgeClient
from wildcam_bridge.settings import get_settings


def test_client_refuses_plain_http() -> None:
    with pytest.raises(ValueError, match="https"):
        BridgeClient("http://wildcam.example.org/bridge", "k")


def test_client_requests() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "POST":
            return httpx.Response(202, json={"id": "J", "status": "queued"})
        if req.url.path.endswith("/results") and req.url.params["format"] == "csv":
            return httpx.Response(200, text="a,b\n")
        if req.url.path.endswith("/results"):
            return httpx.Response(200, json={"rows": []})
        return httpx.Response(200, json={"id": "J", "status": "ready"})

    with BridgeClient("https://h/bridge/", "secret", transport=httpx.MockTransport(handler)) as c:
        assert c.submit(FIXTURES / "sample_query.csv", title="T", dry_run=True)["id"] == "J"
        assert c.wait("J", poll=0)["status"] == "ready"
        assert c.results("J") == {"rows": []}
        assert c.results_csv("J") == "a,b\n"
    post = seen[0]
    assert post.headers["authorization"] == "Bearer secret"
    assert post.url.path == "/bridge/api/v1/jobs"
    assert b'name="dry_run"\r\n\r\ntrue' in post.content
    assert b"sample_query.csv" in post.content


def test_gen_api_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["gen-api-key"]) == 0
    out = capsys.readouterr().out
    assert "wcb_" in out and "sha256:" in out


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_erda: FakeErda) -> None:
    (tmp_path / "known_hosts").write_text(fake_erda.known_hosts_line())
    (tmp_path / "media").mkdir()
    monkeypatch.chdir(tmp_path)  # no .env here
    for k, v in {
        "ERDA_HOST": "127.0.0.1",
        "ERDA_PORT": str(fake_erda.port),
        "ERDA_SHARELINK": SHARE,
        "ERDA_KNOWN_HOSTS": str(tmp_path / "known_hosts"),
        "LS_DATA_ROOT": str(tmp_path / "media"),
        "DISK_RESERVE_GB": "0",
        "BRIDGE_STATE_DIR": str(tmp_path / "var"),
        "LS_LABEL_CONFIG": str(FIXTURES.parent.parent / "config" / "label_config.xml"),
        "LS_URL": "http://127.0.0.1:9",
        "LS_API_TOKEN": "x",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def test_cli_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_erda: FakeErda,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _env(monkeypatch, tmp_path, fake_erda)
    rc = cli.main(["dry-run", str(FIXTURES / "sample_query.csv")])
    out = capsys.readouterr()
    assert rc == 0, out
    assert json.loads(out.out)["n_found"] == 4
    assert "OK: 4/4 files on ERDA" in out.err
    get_settings.cache_clear()


def test_cli_run_dry_and_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_erda: FakeErda,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _env(monkeypatch, tmp_path, fake_erda)
    monkeypatch.setattr(pipeline, "default_ls_factory", lambda s: FakeLabelStudio())
    assert cli.main(["run", "--dry-run", str(FIXTURES / "sample_query.csv")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run_ok"
    # check: ERDA, disk, config and (fake) Label Studio pass; no API key hash -> exit 1
    assert cli.main(["check"]) == 1
    out = capsys.readouterr().out
    for name in ("erda", "label config", "label studio", "disk"):
        assert f"[PASS] {name}" in out
    assert "[FAIL] api keys" in out
    get_settings.cache_clear()


def test_serve_refuses_without_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_erda: FakeErda
) -> None:
    _env(monkeypatch, tmp_path, fake_erda)
    assert cli.main(["serve"]) == 2
    get_settings.cache_clear()


def test_client_cli_needs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRIDGE_URL", raising=False)
    with pytest.raises(SystemExit, match="BRIDGE_URL"):
        cli.main(["client", "status", "x"])


def test_client_delete() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if "/projects/" in req.url.path:
            return httpx.Response(409, json={"detail": "project 4 has 3 annotation(s)"})
        return httpx.Response(200, json={"id": "J", "status": "deleted"})

    with BridgeClient("https://h/bridge", "k", transport=httpx.MockTransport(handler)) as c:
        assert c.delete("J", force=True)["status"] == "deleted"
        with pytest.raises(httpx.HTTPStatusError):
            c.delete_project(4)
    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/bridge/api/v1/jobs/J")
    assert seen[0].url.params["force"] == "true"
    assert seen[1].url.path == "/bridge/api/v1/projects/4"
    assert seen[1].url.params["force"] == "false"


def test_client_cli_delete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params["force"] == "false":
            return httpx.Response(409, json={"detail": "project 4 has 3 annotation(s)"})
        return httpx.Response(200, json={"id": "J", "status": "deleted"})

    monkeypatch.setattr(
        cli,
        "_client",
        lambda: BridgeClient("https://h", "k", transport=httpx.MockTransport(handler)),
    )
    assert cli.main(["client", "delete", "--project", "4"]) == 1
    assert "error 409: project 4 has 3 annotation(s)" in capsys.readouterr().err
    assert cli.main(["client", "delete", "J", "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "deleted"
    with pytest.raises(SystemExit, match="either"):
        cli.main(["client", "delete", "J", "--project", "4"])


def test_cli_local_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_erda: FakeErda,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _env(monkeypatch, tmp_path, fake_erda)
    ls = FakeLabelStudio()
    monkeypatch.setattr(pipeline, "default_ls_factory", lambda s: ls)
    assert cli.main(["run", str(FIXTURES / "sample_query.csv")]) == 0
    job = json.loads(capsys.readouterr().out)
    pid = job["project_id"]
    ls.tasks[pid][0]["annotations"] = [{"id": 1, "result": []}]

    assert cli.main(["delete", "--project", str(pid)]) == 1  # annotated: needs --force
    assert "refused" in capsys.readouterr().err
    assert cli.main(["delete", "--project", str(pid), "--force"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["job_id"], out["label_studio_project"]) == (job["id"], "deleted")
    assert pid not in ls.projects and not (tmp_path / "media" / "bridge" / job["id"]).exists()
    assert cli.main(["delete", "--project", "999"]) == 1
    assert cli.main(["delete", "20261112T120000Z-abcdef"]) == 1
    assert "unknown job" in capsys.readouterr().err
    get_settings.cache_clear()
