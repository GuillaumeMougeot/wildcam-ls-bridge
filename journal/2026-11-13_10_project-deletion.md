# 10: Project deletion through the API; test projects removed

**Date:** 2026-11-13 · **Status:** done ✅ (live since the restart at 09:04 UTC, verified)

## Requests
1. "Delete the test projects 4, 5 and 6."
2. "Would it also be possible to delete a project through the HTTP API?"

## Design
* `DELETE /api/v1/jobs/{id}` and `DELETE /api/v1/projects/{project_id}` (same effect). Both
  delete the LS project (tasks, annotations, predictions, storage) and the job's video folder
  `labelstudio-data/bridge/<job_id>/`.
* **The job record is kept**, with the new status `deleted` and a summary in `report.deleted`
  (when, LS project, annotations deleted, files removed, bytes freed). `input.csv` stays in
  `var/jobs/<id>/`. `project_url` becomes null. Results and progress answer `410`; retry
  answers `409`.
* Safety:
  * Only projects **created by a bridge job** (`JobStore.find_by_project`). In LS Community
    any org member can delete any project, so without this rule the API key could delete the
    real "wildcam" project (2,723 annotations). Any other project answers 404 and is not
    touched.
  * **Annotated projects need `force=true`** (409 otherwise, with the annotation count), so
    results are not lost by accident.
  * Running jobs (`checking`, `downloading`, `importing`) answer 409. A `queued` job is
    cancelled. The worker now moves `queued → checking` under `Pipeline.state_lock` and skips
    jobs that are no longer queued; retry and delete take the same lock, so they cannot
    interleave.
  * Idempotent: a second call reports `"already deleted"` and still removes leftover videos,
    e.g. for a project deleted in the LS UI.
  * An LS error (other than 404) answers 502 and changes nothing: the videos and the status
    are kept.
* LS 1.23 deletes projects synchronously and never touches files in the document root
  (checked in `projects/api.py`, `perform_destroy`), so the bridge removes the folder itself.
* Also: `BridgeClient.delete` / `delete_project`, `wildcam-bridge client delete <job> |
  --project N [--force]`, and a server-side `wildcam-bridge delete …` that works without an
  API key or a running service.

## Tests
* 9 new unit tests: delete and idempotency, `force` protection, foreign project 404,
  running job 409, queued job cancelled and skipped by the worker, LS failure 502 changes
  nothing, store lookup, client, CLI (remote and local). **110 passed**; ruff, mypy
  `--strict` clean.
* e2e on a throwaway LS 1.23: the annotated project answers 409, `force=true` → 200, LS
  answers 404 for the project, the folder is gone, results answer 410. **1 passed** (150 s).

## Test projects 4, 5, 6 deleted
Before deleting, the LS project list was checked. 4, 5 and 6 were the bridge test jobs.
**Project 2 "wildcam"** (no bridge job) and the owner's new **projects 7 and 8 "test7"** were
left alone. The annotations were backed up first to `var/deleted-results/project-{4,5,6}.csv`
(git-ignored, chmod 600): project 4 had the 3 simulated annotations (service account),
project 6 had 1 by user 1 (the owner's pre-fill check, "red deer" accepted).

`uv run wildcam-bridge delete --project N --force`, the same `Pipeline.delete` the API uses:

| project | job | annotations deleted | files removed | freed |
|---|---|---|---|---|
| 4 | `20261112T140452Z-3a357f` | 3 | 95 | 1.36 GB |
| 5 | `20261112T142121Z-cb212c` | 0 | 2 | 8 MB |
| 6 | `20261112T143030Z-1b356f` | 1 | 95 | 1.36 GB |

Afterwards: LS answers 404 for 4, 5 and 6; 2, 7 and 8 are unchanged; LS `/health` 200; free
disk 63 → 65 GB. The running (pre-restart) service shows the three jobs as `deleted`.

## Live check after the owner's restart (09:04 UTC, commit 1bf61ae)
Through `https://wildcam.ecoscience.dk/bridge` with the client key:

| Check | Result |
|---|---|
| `DELETE /projects/999999` | 404 "no bridge job created this project" |
| `DELETE /projects/2` (the real "wildcam" project, no bridge job) | 404, untouched (2,723 annotations) |
| `DELETE` without an API key | 401 |
| `DELETE` an already deleted job | 200, `"already deleted"` |
| one-video job `20261113T090610Z-65e8a6` → project 9, annotated once by the service account | `ready` |
| `DELETE /projects/9` without force | 409 "project 9 has 1 annotation(s); …"; project and folder kept, results still downloadable |
| `client delete <job> --force` | 200: `annotations_deleted 1`, 2 files, 8 MB; `project_url` null |
| afterwards | LS answers 404 for project 9; folder gone; results 410; projects 2, 7 and 8 untouched |
