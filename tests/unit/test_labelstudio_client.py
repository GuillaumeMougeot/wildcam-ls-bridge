import base64
import json
import time
from typing import Any

import httpx
import pytest

from wildcam_bridge.labelstudio import NEVER_MATCH_REGEX, LabelStudioClient, LabelStudioError


def _jwt(exp: float) -> str:
    def enc(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'HS256'})}.{enc({'exp': exp, 'token_type': 'refresh'})}.sig"


def test_legacy_token_header() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": 3})

    with LabelStudioClient("http://ls", "abc123", transport=httpx.MockTransport(handler)) as c:
        assert c.whoami() == {"id": 3}
    assert seen[0].headers["authorization"] == "Token abc123"


def test_pat_is_exchanged_and_refreshed_on_401() -> None:
    pat = _jwt(time.time() + 3600)
    calls: list[str] = []
    state = {"n_access": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}")
        if req.url.path == "/api/token/refresh/":
            assert json.loads(req.content) == {"refresh": pat}
            state["n_access"] += 1
            return httpx.Response(
                200, json={"access": _jwt(time.time() + 300) + str(state["n_access"])}
            )
        auth = req.headers["authorization"]
        assert auth.startswith("Bearer ")
        if auth.endswith("1"):  # first access token: pretend it was revoked
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"id": 9})

    with LabelStudioClient("http://ls", pat, transport=httpx.MockTransport(handler)) as c:
        assert c.whoami() == {"id": 9}
        assert c.whoami() == {"id": 9}  # cached access token reused
    assert calls == [
        "POST /api/token/refresh/",
        "GET /api/current-user/whoami",
        "POST /api/token/refresh/",
        "GET /api/current-user/whoami",
        "GET /api/current-user/whoami",
    ]


def test_bad_pat() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad"})

    with (
        LabelStudioClient("http://ls", _jwt(1), transport=httpx.MockTransport(handler)) as c,
        pytest.raises(LabelStudioError, match="Personal Access Token rejected"),
    ):
        c.whoami()


def test_errors_and_missing_token() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    with LabelStudioClient("http://ls", "t", transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(LabelStudioError) as info:
            c.get_project(5)
        assert info.value.status_code == 404
    with pytest.raises(LabelStudioError, match="LS_API_TOKEN"):
        LabelStudioClient("http://ls", "")


def test_unreachable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with (
        LabelStudioClient("http://ls", "t", transport=httpx.MockTransport(handler)) as c,
        pytest.raises(LabelStudioError, match="unreachable"),
    ):
        c.whoami()


def test_request_payloads() -> None:
    seen: list[tuple[str, str, dict[str, str], Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, dict(req.url.params), body))
        if req.url.path == "/api/projects/4":
            return httpx.Response(
                200, json={"title": "t", "task_number": 3, "finished_task_number": 3}
            )
        return httpx.Response(201, json=[] if req.method == "GET" else {"id": 4})

    with LabelStudioClient("http://ls/", "t", transport=httpx.MockTransport(handler)) as c:
        c.create_project("x" * 80, "<View/>", "d")
        c.create_local_storage(4, "/data/bridge/j", "title")
        c.import_tasks(4, [{"data": {"video": "v"}}])
        c.export_tasks(4)
        c.list_local_storages(4)
        progress = c.project_progress(4)
    assert seen[0][3]["title"] == "x" * 50  # LS limits titles to 50 chars
    assert seen[1][3]["regex_filter"] == NEVER_MATCH_REGEX
    assert seen[1][3]["use_blob_urls"] is False
    assert seen[2][2] == {"return_task_ids": "true"}
    assert seen[3][2] == {"exportType": "JSON", "download_all_tasks": "true"}
    assert seen[4][2] == {"project": "4"}
    assert progress["complete"] is True
