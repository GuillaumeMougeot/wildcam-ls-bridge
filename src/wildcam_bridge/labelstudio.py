"""Minimal Label Studio REST client (tested against Label Studio 1.23 Community).

Authentication
--------------
* Personal Access Token (PAT, default in LS >= 1.18 and the only kind enabled on the
  wildcam server): a long-lived JWT *refresh* token. It is exchanged at
  ``POST /api/token/refresh/`` for a short-lived access token sent as ``Bearer``.
* Legacy token: sent as ``Authorization: Token <key>`` (disabled on the wildcam org).

Local files
-----------
LS only serves ``/data/local-files/?d=...`` to users of a project that owns a *Local files*
storage whose path is a prefix of the file's folder. We therefore create one storage per
project pointing at the job folder. We import tasks through the API instead of syncing the
storage (deterministic, returns counts), so the storage's regex matches nothing and an
accidental "Sync" click cannot duplicate tasks.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

NEVER_MATCH_REGEX = "(?!)"


class LabelStudioError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _jwt_exp(token: str) -> float | None:
    """Return the ``exp`` claim of a JWT (unverified), or None if ``token`` is not a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp") if isinstance(payload, dict) else None
    return float(exp) if isinstance(exp, int | float) else 0.0


class LabelStudioClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token:
            raise LabelStudioError("LS_API_TOKEN is not set (.env)")
        self._token = token
        self._is_pat = _jwt_exp(token) is not None
        self._access: str | None = None
        self._access_exp = 0.0
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LabelStudioClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ auth
    def _refresh_access(self) -> None:
        resp = self._http.post("/api/token/refresh/", json={"refresh": self._token})
        if resp.status_code != 200:
            raise LabelStudioError(
                f"Personal Access Token rejected by Label Studio ({resp.status_code})",
                resp.status_code,
            )
        self._access = resp.json()["access"]
        self._access_exp = _jwt_exp(self._access) or (time.time() + 60)

    def _auth_header(self) -> dict[str, str]:
        if not self._is_pat:
            return {"Authorization": f"Token {self._token}"}
        if self._access is None or time.time() > self._access_exp - 30:
            self._refresh_access()
        return {"Authorization": f"Bearer {self._access}"}

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        extra_headers = kwargs.pop("headers", {})
        for attempt in (1, 2):
            headers = {**extra_headers, **self._auth_header()}
            try:
                resp = self._http.request(method, path, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise LabelStudioError(f"Label Studio unreachable: {exc}") from exc
            if resp.status_code == 401 and self._is_pat and attempt == 1:
                self._access = None  # access token expired early -> refresh once
                continue
            if resp.status_code >= 400:
                raise LabelStudioError(
                    f"{method} {path} -> {resp.status_code}: {resp.text[:500]}", resp.status_code
                )
            return resp
        raise AssertionError("unreachable")  # pragma: no cover

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.request(method, path, **kwargs).json()

    # ------------------------------------------------------------------ endpoints
    def whoami(self) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", "/api/current-user/whoami")
        return result

    def create_project(
        self, title: str, label_config: str, description: str = ""
    ) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            "POST",
            "/api/projects",
            json={"title": title[:50], "description": description, "label_config": label_config},
        )
        return result

    def get_project(self, project_id: int) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", f"/api/projects/{project_id}")
        return result

    def update_project(self, project_id: int, **fields: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._json("PATCH", f"/api/projects/{project_id}", json=fields)
        return result

    def delete_project(self, project_id: int) -> None:
        self.request("DELETE", f"/api/projects/{project_id}")

    def create_local_storage(self, project_id: int, path: str, title: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            "POST",
            "/api/storages/localfiles",
            json={
                "project": project_id,
                "path": path,
                "title": title[:256],
                "regex_filter": NEVER_MATCH_REGEX,
                "use_blob_urls": False,
                "description": "Created by wildcam-ls-bridge for media access. Do NOT sync.",
            },
        )
        return result

    def list_local_storages(self, project_id: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._json(
            "GET", "/api/storages/localfiles", params={"project": project_id}
        )
        return result

    def import_tasks(self, project_id: int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            "POST",
            f"/api/projects/{project_id}/import",
            params={"return_task_ids": "true"},
            json=tasks,
        )
        return result

    def export_tasks(
        self, project_id: int, *, download_all_tasks: bool = True
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._json(
            "GET",
            f"/api/projects/{project_id}/export",
            params={
                "exportType": "JSON",
                "download_all_tasks": "true" if download_all_tasks else "false",
            },
        )
        return result

    def project_progress(self, project_id: int) -> dict[str, Any]:
        p = self.get_project(project_id)
        total = int(p.get("task_number") or 0)
        finished = int(p.get("finished_task_number") or 0)
        return {
            "project_id": project_id,
            "title": p.get("title"),
            "total_tasks": total,
            "finished_tasks": finished,
            "tasks_with_annotations": int(p.get("num_tasks_with_annotations") or 0),
            "total_annotations": int(p.get("total_annotations_number") or 0),
            "skipped_annotations": int(p.get("skipped_annotations_number") or 0),
            "complete": total > 0 and finished >= total,
        }
