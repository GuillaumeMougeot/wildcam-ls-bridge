"""The end-to-end pipeline run for each job.

CSV  ->  parse/validate  ->  DRY RUN (ERDA login, stat files, free disk space)
     ->  download (SFTP)  ->  Label Studio project (+ local storage + tasks)  ->  READY
                                    ...human annotation...
GET results  <-  Label Studio export (flattened)
DELETE job   ->  Label Studio project deleted + job folder removed (job record kept)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wildcam_bridge import erda
from wildcam_bridge.csv_ingest import parse_csv
from wildcam_bridge.jobs import Job, JobStatus, JobStore, utcnow
from wildcam_bridge.label_config import LabelConfig, load_label_config, parse_label_config
from wildcam_bridge.labelstudio import LabelStudioClient, LabelStudioError
from wildcam_bridge.paths import job_media_dir, validate_job_id
from wildcam_bridge.results import columns_for, flatten_export
from wildcam_bridge.settings import Settings
from wildcam_bridge.tasks import build_tasks

log = logging.getLogger(__name__)

LSClientFactory = Callable[[Settings], LabelStudioClient]


def default_ls_factory(settings: Settings) -> LabelStudioClient:
    return LabelStudioClient(
        settings.ls_url, settings.ls_api_token.get_secret_value(), timeout=settings.ls_timeout
    )


class JobFailed(RuntimeError):
    pass


class DeleteRefused(RuntimeError):
    """Deletion refused: job still running, or project has annotations and force=False."""


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        ls_factory: LSClientFactory | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.ls_factory = ls_factory or default_ls_factory
        self._label_config: LabelConfig | None = None
        # Serialises job state transitions (worker start, retry, delete) inside one process.
        self.state_lock = threading.Lock()

    @property
    def label_config(self) -> LabelConfig:
        if self._label_config is None:
            self._label_config = load_label_config(self.settings.ls_label_config)
        return self._label_config

    # ------------------------------------------------------------------ inputs
    def job_state_dir(self, job_id: str) -> Path:
        return self.settings.jobs_state_root / validate_job_id(job_id)

    def save_input(self, job_id: str, csv_bytes: bytes) -> Path:
        path = self.job_state_dir(job_id) / "input.csv"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(csv_bytes)
        return path

    # ------------------------------------------------------------------ processing
    def process(self, job_id: str) -> None:
        """Run one job to completion. Never raises: failures are stored on the job."""
        with self.state_lock:  # a queued job may have been deleted (cancelled) meanwhile
            job = self.store.get(job_id)
            if job is None:
                log.error("job %s vanished", job_id)
                return
            if job.status != JobStatus.QUEUED:
                log.warning("job %s is %s, not queued: skipped", job_id, job.status)
                return
            self.store.update(job_id, status=JobStatus.CHECKING, progress={"stage": "dry_run"})
        try:
            self._run(job)
        except Exception as exc:
            log.exception("job %s failed", job_id)
            self.store.update(job_id, status=JobStatus.FAILED, error=str(exc) or type(exc).__name__)

    def _set(self, job: Job, **fields: Any) -> None:
        self.store.update(job.id, **fields)

    def _run(self, job: Job) -> None:
        s = self.settings
        csv_bytes = (self.job_state_dir(job.id) / "input.csv").read_bytes()
        parsed = parse_csv(csv_bytes, strip=s.erda_strip_prefix, max_rows=s.bridge_max_rows)
        files = erda.plan_files(s, job.id, parsed.items)

        # 1. DRY RUN ----------------------------------------------------------------
        self._set(job, status=JobStatus.CHECKING, progress={"stage": "dry_run"})
        # Fail fast on a bad Label Studio token, before downloading gigabytes.
        try:
            with self.ls_factory(s) as ls:
                ls.whoami()
        except LabelStudioError as exc:
            raise JobFailed(f"Label Studio check failed: {exc}") from exc
        report = asyncio.run(erda.dry_run(s, files))
        self._set(job, report={"dry_run": report.to_dict()})
        if report.errors:
            raise JobFailed("dry run failed: " + "; ".join(report.errors[:5]))
        if report.missing and not job.allow_missing:
            raise JobFailed(
                f"{len(report.missing)} of {report.n_files} file(s) not found on ERDA "
                "(see report.dry_run.missing_sample; resubmit with allow_missing=true to skip them)"
            )
        if report.n_found == 0:
            raise JobFailed("none of the files exist on ERDA")
        if not report.enough_space:
            raise JobFailed(
                f"not enough disk space: need {report.bytes_to_download} B + reserve "
                f"{report.reserve_bytes} B, free {report.free_bytes} B"
            )
        if job.dry_run:
            self._set(job, status=JobStatus.DRY_RUN_OK, progress={"stage": "done"})
            return

        # 2. DOWNLOAD ----------------------------------------------------------------
        self._set(job, status=JobStatus.DOWNLOADING, progress={"stage": "download"})

        def on_progress(p: dict[str, Any]) -> None:
            self._set(job, progress={"stage": "download", **p})

        dl = asyncio.run(erda.download(s, files, on_progress))
        self._set(job, report={"dry_run": report.to_dict(), "download": dl.to_dict()})
        if dl.failed:
            raise JobFailed(f"{len(dl.failed)} file(s) failed to download; retry the job")

        # 3. LABEL STUDIO PROJECT ----------------------------------------------------------
        self._set(job, status=JobStatus.IMPORTING, progress={"stage": "label_studio"})
        found_ids = {f.media_id for f in files if f.found}
        items = [i for i in parsed.items if i.media_id in found_ids]
        tasks, task_warnings = build_tasks(
            items, job_id=job.id, settings=s, label_config=self.label_config
        )
        media_dir = job_media_dir(s, job.id)
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / "tasks.json").write_text(
            json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        warnings = [*job.warnings, *task_warnings]
        if report.missing:
            warnings.append(f"{len(report.missing)} file(s) missing on ERDA were skipped")

        with self.ls_factory(s) as ls:
            project_id = job.project_id
            if project_id is None:
                description = (
                    f"{job.description}\n\n" if job.description else ""
                ) + f"Created by wildcam-ls-bridge, job {job.id} ({len(tasks)} videos)."
                project = ls.create_project(job.title, self.label_config.xml, description)
                project_id = int(project["id"])
                self._set(job, project_id=project_id)
            if not ls.list_local_storages(project_id):
                ls.create_local_storage(
                    project_id, str(media_dir.resolve()), f"wildcam-bridge {job.id}"
                )
            if int(ls.get_project(project_id).get("task_number") or 0) == 0:
                result = ls.import_tasks(project_id, tasks)
                log.info(
                    "job %s: imported %s",
                    job.id,
                    {k: result.get(k) for k in ("task_count", "prediction_count")},
                )
            model_versions = sorted(
                {p["model_version"] for t in tasks for p in t.get("predictions", [])}
            )
            if model_versions:
                # Pre-fill the annotation form with the model's guess. The LS 1.23 frontend
                # builds the first annotation from a prediction when show_collab_predictions
                # is on, the prediction's model_version is the project's, and pre-annotations
                # are not "revealed interactively" (docs/reference/label-studio-notes.md).
                # Tasks whose CSV row had no (usable) prediction simply open blank.
                try:
                    ls.update_project(
                        project_id,
                        show_collab_predictions=True,
                        model_version=model_versions[0],
                        reveal_preannotations_interactively=False,
                    )
                except Exception as exc:  # cosmetic; do not fail the job
                    warnings.append(f"could not enable pre-annotations: {exc}")

        self._set(
            job,
            status=JobStatus.READY,
            warnings=warnings,
            progress={"stage": "annotation", "tasks": len(tasks)},
        )

    # ------------------------------------------------------------------ deletion
    def delete(self, job_id: str, *, force: bool = False) -> dict[str, Any]:
        """Delete a job's Label Studio project and its downloaded videos.

        The job record (and its input CSV) is kept with status ``deleted``, so the deletion
        stays traceable. Idempotent: repeating it finishes a partial deletion. Refused for
        running jobs, and for projects with annotations unless ``force`` (annotations are
        the valuable output: download the results first). A queued job is simply cancelled.
        """
        with self.state_lock:
            job = self.store.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in JobStatus.RUNNING:
                raise DeleteRefused(f"job is {job.status}; delete it once it is ready or failed")
            summary: dict[str, Any] = {
                "at": utcnow(),
                "project_id": job.project_id,
                "label_studio_project": None,
                "annotations_deleted": 0,
                "files_removed": 0,
                "bytes_freed": 0,
            }
            if job.project_id is not None:
                summary.update(self._delete_ls_project(job.project_id, force=force))
            media_dir = job_media_dir(self.settings, job.id)
            if media_dir.is_dir() and not media_dir.is_symlink():
                files = [f for f in media_dir.rglob("*") if f.is_file() and not f.is_symlink()]
                summary["files_removed"] = len(files)
                summary["bytes_freed"] = sum(f.stat().st_size for f in files)
                shutil.rmtree(media_dir)
            self.store.update(
                job.id,
                status=JobStatus.DELETED,
                progress={"stage": "deleted"},
                report={**job.report, "deleted": summary},
            )
            log.info("job %s deleted: %s", job.id, summary)
            return summary

    def _delete_ls_project(self, project_id: int, *, force: bool) -> dict[str, Any]:
        with self.ls_factory(self.settings) as ls:
            try:
                project = ls.get_project(project_id)
            except LabelStudioError as exc:
                if exc.status_code == 404:  # deleted earlier (in the UI or a previous call)
                    return {"label_studio_project": "already deleted"}
                raise
            n = int(project.get("total_annotations_number") or 0)
            if n and not force:
                raise DeleteRefused(
                    f"project {project_id} has {n} annotation(s); download the results first, "
                    "then repeat with force=true"
                )
            ls.delete_project(project_id)  # synchronous; LS cascades tasks and storages
        return {"label_studio_project": "deleted", "annotations_deleted": n}

    # ------------------------------------------------------------------ results
    def results(
        self, project_id: int, *, include_unannotated: bool = False
    ) -> tuple[list[dict[str, Any]], list[str]]:
        with self.ls_factory(self.settings) as ls:
            # Use the project's own labeling config: projects may differ from the template.
            xml = ls.get_project(project_id).get("label_config")
            config = parse_label_config(xml) if xml else self.label_config
            exported = ls.export_tasks(project_id, download_all_tasks=True)
        rows = flatten_export(
            exported, config, project_id=project_id, include_unannotated=include_unannotated
        )
        return rows, columns_for(config)

    def progress(self, project_id: int) -> dict[str, Any]:
        with self.ls_factory(self.settings) as ls:
            return ls.project_progress(project_id)
