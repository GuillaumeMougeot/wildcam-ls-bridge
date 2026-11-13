"""Small Python client for the bridge API, for the SQL/database side.

    from wildcam_bridge.client import BridgeClient
    with BridgeClient("https://wildcam.ecoscience.dk/bridge", api_key) as c:
        job = c.submit("query.csv", title="Hoestemark Dec 2024")
        job = c.wait(job["id"])                  # until ready / failed
        rows = c.results(job["id"])["rows"]      # when annotators are done
        c.delete(job["id"])                      # project + videos, once results are stored

Only HTTPS URLs are accepted (except localhost, for tests).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

TERMINAL = {"ready", "dry_run_ok", "failed", "deleted"}


class BridgeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "testserver",
        }:
            raise ValueError("refusing to send the API key over plain HTTP; use an https:// URL")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BridgeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> httpx.Response:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        return resp

    def submit(
        self,
        csv_path: str | Path,
        *,
        title: str = "",
        description: str = "",
        dry_run: bool = False,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        path = Path(csv_path)
        with path.open("rb") as fh:
            resp = self._http.post(
                "/api/v1/jobs",
                files={"file": (path.name, fh, "text/csv")},
                data={
                    "title": title,
                    "description": description,
                    "dry_run": str(dry_run).lower(),
                    "allow_missing": str(allow_missing).lower(),
                },
            )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    def job(self, job_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._get(f"/api/v1/jobs/{job_id}").json()
        return result

    def wait(
        self, job_id: str, *, poll: float = 10.0, timeout: float = 24 * 3600
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            job = self.job(job_id)
            if job["status"] in TERMINAL or time.monotonic() > deadline:
                return job
            time.sleep(poll)

    def progress(self, job_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._get(f"/api/v1/jobs/{job_id}/progress").json()
        return result

    def results(self, job_id: str, *, include_unannotated: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = self._get(
            f"/api/v1/jobs/{job_id}/results",
            format="json",
            include_unannotated=str(include_unannotated).lower(),
        ).json()
        return result

    def delete(self, job_id: str, *, force: bool = False) -> dict[str, Any]:
        """Delete the job's Label Studio project and videos (``force``: even if annotated)."""
        return self._delete(f"/api/v1/jobs/{job_id}", force)

    def delete_project(self, project_id: int, *, force: bool = False) -> dict[str, Any]:
        return self._delete(f"/api/v1/projects/{project_id}", force)

    def _delete(self, path: str, force: bool) -> dict[str, Any]:
        resp = self._http.delete(path, params={"force": str(force).lower()})
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    def results_csv(self, job_id: str, *, include_unannotated: bool = False) -> str:
        return self._get(
            f"/api/v1/jobs/{job_id}/results",
            format="csv",
            include_unannotated=str(include_unannotated).lower(),
        ).text
