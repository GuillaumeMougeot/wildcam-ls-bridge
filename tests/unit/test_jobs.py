import threading
from pathlib import Path

import pytest

from wildcam_bridge.jobs import JobRunner, JobStatus, JobStore, new_job_id
from wildcam_bridge.paths import validate_job_id


def test_job_ids_are_fixed_length_and_valid() -> None:
    ids = {new_job_id() for _ in range(50)}
    assert len(ids) == 50
    assert {len(i) for i in ids} == {23}
    for i in ids:
        validate_job_id(i)


def _create(store: JobStore, **kw: object) -> str:
    args = {
        "title": "t",
        "description": "",
        "dry_run": False,
        "allow_missing": False,
        "n_items": 2,
        "warnings": ["w"],
    }
    args.update(kw)
    return store.create(**args).id  # type: ignore[arg-type]


def test_store_roundtrip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "db.sqlite3")
    job_id = _create(store)
    store.update(job_id, status=JobStatus.READY, project_id=12, progress={"stage": "x"})
    job = store.get(job_id)
    assert job is not None
    assert (job.status, job.project_id, job.progress, job.warnings) == (
        "ready",
        12,
        {"stage": "x"},
        ["w"],
    )
    assert job.to_public("https://ls")["project_url"] == "https://ls/projects/12/data"
    assert store.get("nope") is None
    assert [j.id for j in store.recent()] == [job_id]
    with pytest.raises(ValueError, match="cannot update"):
        store.update(job_id, title="x")


def test_fail_interrupted_and_queued(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "db.sqlite3")
    running = _create(store)
    store.update(running, status=JobStatus.DOWNLOADING)
    queued = _create(store)
    done = _create(store)
    store.update(done, status=JobStatus.READY)
    assert store.fail_interrupted() == [running]
    job = store.get(running)
    assert job is not None and job.status == JobStatus.FAILED and "restart" in (job.error or "")
    assert store.queued_ids() == [queued]


def test_find_by_project_and_deleted_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "db.sqlite3")
    job_id = _create(store)
    store.update(job_id, status=JobStatus.READY, project_id=7)
    found = store.find_by_project(7)
    assert found is not None and found.id == job_id
    assert store.find_by_project(8) is None
    store.update(job_id, status=JobStatus.DELETED)
    assert store.fail_interrupted() == []  # deleted is terminal: not touched on restart
    job = store.get(job_id)
    assert job is not None
    public = job.to_public("https://ls")
    assert (public["project_id"], public["project_url"]) == (7, None)  # no dead link


def test_runner_processes_sequentially_and_survives_errors() -> None:
    seen: list[str] = []
    active = threading.Semaphore(1)

    def handler(job_id: str) -> None:
        assert active.acquire(blocking=False), "jobs must not run concurrently"
        try:
            seen.append(job_id)
            if job_id == "boom":
                raise RuntimeError("boom")
        finally:
            active.release()

    runner = JobRunner(handler)
    runner.start()
    for j in ("a", "boom", "b"):
        runner.submit(j)
    runner.join_idle()
    runner.stop()
    assert seen == ["a", "boom", "b"]
