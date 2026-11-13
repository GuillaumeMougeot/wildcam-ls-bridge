"""HTTP API (FastAPI).

Exposed by nginx at ``https://wildcam.ecoscience.dk/bridge/`` (TLS terminated by nginx);
the app itself only listens on 127.0.0.1. Every ``/api/v1`` route requires
``Authorization: Bearer <api-key>``. See docs/user-guide.md.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from wildcam_bridge import __version__
from wildcam_bridge.auth import verify_api_key
from wildcam_bridge.csv_ingest import CsvValidationError, parse_csv
from wildcam_bridge.jobs import Job, JobRunner, JobStatus, JobStore, new_job_id
from wildcam_bridge.labelstudio import LabelStudioError
from wildcam_bridge.pipeline import DeleteRefused, LSClientFactory, Pipeline
from wildcam_bridge.results import rows_to_csv
from wildcam_bridge.settings import Settings, get_settings

log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)
# Module level on purpose: with postponed annotations FastAPI resolves names in module globals.
Fmt = Annotated[Literal["json", "csv"], Query(alias="format", description="json or csv")]
Force = Annotated[bool, Query(description="also delete a project that already has annotations")]


def _route_path(request: Request) -> str:
    """Path without the ASGI root_path (uvicorn --root-path prefixes scope["path"] with it)."""
    path: str = request.scope.get("path", "")
    root: str = request.scope.get("root_path", "")
    return path[len(root) :] if root and path.startswith(root) else path


def create_app(
    settings: Settings | None = None,
    *,
    ls_factory: LSClientFactory | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    store = JobStore(settings.db_path)
    pipeline = Pipeline(settings, store, ls_factory)
    runner = JobRunner(pipeline.process)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        interrupted = store.fail_interrupted()
        if interrupted:
            log.warning("marked interrupted jobs as failed: %s", interrupted)
        if start_worker:
            runner.start()
            for job_id in store.queued_ids():
                runner.submit(job_id)
        yield
        if start_worker:
            runner.stop()

    docs = settings.bridge_enable_docs
    app = FastAPI(
        title="wildcam Label Studio bridge",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.pipeline = pipeline
    app.state.runner = runner

    # ------------------------------------------------------------------ security
    @app.middleware("http")
    async def enforce_https(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # nginx terminates TLS and sets X-Forwarded-Proto. The app listens on 127.0.0.1
        # only, so this header cannot be forged by remote clients.
        if (
            settings.bridge_require_https
            and _route_path(request) != "/healthz"
            and request.headers.get("x-forwarded-proto", "").lower() != "https"
        ):
            return JSONResponse({"detail": "HTTPS required"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def require_api_key(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        if creds is None or not verify_api_key(creds.credentials, settings.api_key_hashes):
            raise HTTPException(
                status_code=401,
                detail="invalid or missing API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(require_api_key)]

    def get_job_or_404(job_id: str) -> Job:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    def public(job: Job) -> dict[str, Any]:
        return job.to_public(settings.ls_public_url)

    def ls_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except LabelStudioError as exc:
            status = 404 if exc.status_code == 404 else 502
            raise HTTPException(status_code=status, detail=f"Label Studio: {exc}") from exc

    # ------------------------------------------------------------------ routes
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/jobs", status_code=202, dependencies=auth, tags=["jobs"])
    async def submit_job(
        file: Annotated[UploadFile, File(description="CSV exported from the wildcam SQL DB")],
        title: Annotated[str, Form(max_length=50)] = "",
        description: Annotated[str, Form(max_length=2000)] = "",
        dry_run: Annotated[bool, Form()] = False,
        allow_missing: Annotated[bool, Form()] = False,
    ) -> dict[str, Any]:
        """Submit a CSV: the job dry-runs, downloads, then builds a Label Studio project."""
        data = await file.read(settings.bridge_max_csv_bytes + 1)
        if len(data) > settings.bridge_max_csv_bytes:
            raise HTTPException(status_code=413, detail="CSV too large")
        try:
            parsed = parse_csv(
                data, strip=settings.erda_strip_prefix, max_rows=settings.bridge_max_rows
            )
        except CsvValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job_id = new_job_id()
        pipeline.save_input(job_id, data)
        job = store.create(
            job_id=job_id,
            title=title.strip() or f"wildcam {job_id}",
            description=description,
            dry_run=dry_run,
            allow_missing=allow_missing,
            n_items=len(parsed.items),
            warnings=parsed.warnings,
        )
        runner.submit(job.id)
        return public(job)

    @app.get("/api/v1/jobs", dependencies=auth, tags=["jobs"])
    def list_jobs(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[dict[str, Any]]:
        return [public(j) for j in store.recent(limit)]

    @app.get("/api/v1/jobs/{job_id}", dependencies=auth, tags=["jobs"])
    def get_job(job_id: str) -> dict[str, Any]:
        return public(get_job_or_404(job_id))

    @app.post("/api/v1/jobs/{job_id}/retry", status_code=202, dependencies=auth, tags=["jobs"])
    def retry_job(job_id: str) -> dict[str, Any]:
        """Re-run a failed job. Files already downloaded (same size) are not fetched again."""
        with pipeline.state_lock:  # not concurrently with a DELETE of the same job
            job = get_job_or_404(job_id)
            if job.status != JobStatus.FAILED:
                raise HTTPException(status_code=409, detail=f"job is {job.status}, not failed")
            store.update(job_id, status=JobStatus.QUEUED, error=None)
        runner.submit(job_id)
        return public(get_job_or_404(job_id))

    def delete_response(job_id: str, force: bool) -> dict[str, Any]:
        try:
            ls_call(lambda: pipeline.delete(job_id, force=force))
        except DeleteRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return public(get_job_or_404(job_id))

    @app.delete("/api/v1/jobs/{job_id}", dependencies=auth, tags=["jobs"])
    def delete_job(job_id: str, force: Force = False) -> dict[str, Any]:
        """Delete the job's Label Studio project and downloaded videos (the job record stays,
        with status ``deleted``). A queued job is cancelled; a running one is refused (409)."""
        get_job_or_404(job_id)
        return delete_response(job_id, force)

    @app.delete("/api/v1/projects/{project_id}", dependencies=auth, tags=["jobs"])
    def delete_project(project_id: int, force: Force = False) -> dict[str, Any]:
        """Same as ``DELETE /jobs/{id}``, by Label Studio project id. Only projects created by
        the bridge can be deleted here; others are left alone (404)."""
        job = store.find_by_project(project_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail="no bridge job created this project; delete it in Label Studio instead",
            )
        return delete_response(job.id, force)

    def ready_project(job_id: str) -> int:
        job = get_job_or_404(job_id)
        if job.status == JobStatus.DELETED:
            raise HTTPException(status_code=410, detail="job was deleted")
        if job.status != JobStatus.READY or job.project_id is None:
            raise HTTPException(status_code=409, detail=f"job is {job.status}, not ready")
        return job.project_id

    @app.get("/api/v1/jobs/{job_id}/progress", dependencies=auth, tags=["results"])
    def job_progress(job_id: str) -> dict[str, Any]:
        """Annotation progress of the job's Label Studio project (complete = all tasks done)."""
        project_id = ready_project(job_id)
        return {"job_id": job_id, **ls_call(lambda: pipeline.progress(project_id))}

    @app.get("/api/v1/projects/{project_id}/progress", dependencies=auth, tags=["results"])
    def project_progress(project_id: int) -> dict[str, Any]:
        result: dict[str, Any] = ls_call(lambda: pipeline.progress(project_id))
        return result

    def results_response(
        project_id: int, fmt: str, include_unannotated: bool, filename: str
    ) -> Response:
        rows, columns = ls_call(
            lambda: pipeline.results(project_id, include_unannotated=include_unannotated)
        )
        if fmt == "csv":
            return PlainTextResponse(
                rows_to_csv(rows, columns),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
            )
        return JSONResponse({"project_id": project_id, "columns": columns, "rows": rows})

    @app.get("/api/v1/jobs/{job_id}/results", dependencies=auth, tags=["results"])
    def job_results(job_id: str, fmt: Fmt = "json", include_unannotated: bool = False) -> Response:
        """Annotation results, one row per annotation (JSON or CSV)."""
        project_id = ready_project(job_id)
        return results_response(project_id, fmt, include_unannotated, f"results-{job_id}")

    @app.get("/api/v1/projects/{project_id}/results", dependencies=auth, tags=["results"])
    def project_results(
        project_id: int, fmt: Fmt = "json", include_unannotated: bool = False
    ) -> Response:
        """Results of any Label Studio project (also ones not created by the bridge)."""
        return results_response(
            project_id, fmt, include_unannotated, f"results-project-{project_id}"
        )

    return app
