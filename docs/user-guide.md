# User guide: the bridge HTTP API

This guide is for whoever sends video selections from the wildcam database and collects
the annotations. (How to annotate in Label Studio is covered by the team's Label Studio
guide.)

```
 your SQL query ──CSV──▶ POST /jobs ──▶ dry run ─▶ download from ERDA ─▶ Label Studio project
                                                                              │
                                                                   annotators label videos
                                                                              │
 your database ◀──JSON/CSV── GET /jobs/{id}/results ◀───────────────────────┘
```

**Base URL:** `https://wildcam.ecoscience.dk/bridge`
**Interactive docs (OpenAPI):** `https://wildcam.ecoscience.dk/bridge/docs`

## 1. Security

* **HTTPS only.** Everything travels over TLS. Plain-HTTP requests are redirected or refused
  (`403 HTTPS required`), and the Python client refuses `http://` URLs.
* **API key.** Every `/api/v1/...` call needs `Authorization: Bearer <api-key>`. Ask the
  server administrator for a key. Keep it out of scripts and repositories: store it in an
  environment variable or a secret store. A missing or wrong key returns `401`.
* **Rate limit:** 5 requests/s per IP (bursts of 20). Beyond that you get `429`.

```bash
export BRIDGE_URL=https://wildcam.ecoscience.dk/bridge
export BRIDGE_API_KEY=wcb_...        # from the administrator
```

## 2. Prepare the CSV

Export your SQL query as CSV (UTF-8, header row). Requirements:

* columns **`media_id`** and **`file`** (e.g. `wildcams/data/hoestemark/hoestemark_0001/.../img_0093.mp4`);
* optional but recommended: `prediction`, `confidence`, `model_name`, `model_version`,
  `prediction_id`, `file_name`, `media_timestamp_local`, `deployment_id`, `location_name`,
  `site_name`, `file_mediatype`;
* one row per prediction is fine: rows are grouped by `media_id` (the most confident
  prediction becomes the pre-annotation);
* max 20 MB / 20,000 rows per upload.

Full column reference: [reference/csv-schema.md](reference/csv-schema.md). The format of
`resources/test_query.csv` is exactly what the bridge expects.

## 3. Submit a job

```bash
curl -sS -X POST "$BRIDGE_URL/api/v1/jobs" \
  -H "Authorization: Bearer $BRIDGE_API_KEY" \
  -F "file=@query.csv" \
  -F "title=Hoestemark 0001 Dec 2024" \
  -F "description=Red deer check, batch 1"
```

| Form field | Default | Meaning |
|---|---|---|
| `file` | required | the CSV |
| `title` | `wildcam <job_id>` | Label Studio project title (≤ 50 characters) |
| `description` | "" | project description |
| `dry_run` | `false` | `true` = only check ERDA access, file presence and disk space; download nothing |
| `allow_missing` | `false` | `true` = skip files missing on ERDA instead of failing the job |

The CSV is validated immediately (`400` with a message if it is malformed or contains an
unsafe path). The answer is `202 Accepted` with the job:

```json
{"id": "20261112T140501Z-3fa9c1", "status": "queued", "n_items": 94, "project_id": null, ...}
```

Jobs run one at a time, in order.

## 4. Follow the job

```bash
curl -sS "$BRIDGE_URL/api/v1/jobs/20261112T140501Z-3fa9c1" -H "Authorization: Bearer $BRIDGE_API_KEY"
```

| `status` | Meaning |
|---|---|
| `queued` | waiting for the worker |
| `checking` | **dry run**: SFTP login to ERDA, size of every file, free disk space (plus a 10 GB reserve) |
| `downloading` | fetching videos over SFTP; `progress.files_done / files_total` |
| `importing` | creating the Label Studio project and tasks |
| `ready` | project exists: `project_url` links to it; annotation can start |
| `dry_run_ok` | dry-run job passed: a real job would succeed |
| `failed` | see `error`; fix the cause and `POST /api/v1/jobs/{id}/retry` (already downloaded files are kept) |

Useful fields: `report.dry_run` (files found, missing sample, bytes to download, free
space), `report.download`, `warnings` (e.g. a model label that is not a choice in the UI),
`project_id`, `project_url`.

Other job endpoints: `GET /api/v1/jobs?limit=50` (latest jobs) and
`POST /api/v1/jobs/{id}/retry` (failed jobs only).

## 5. Annotation progress

```bash
curl -sS "$BRIDGE_URL/api/v1/jobs/$JOB/progress" -H "Authorization: Bearer $BRIDGE_API_KEY"
```

```json
{"job_id": "...", "project_id": 12, "total_tasks": 94, "finished_tasks": 40,
 "tasks_with_annotations": 40, "total_annotations": 41, "skipped_annotations": 0, "complete": false}
```

`complete` becomes `true` when every task has been annotated.

## 6. Get the results

```bash
# JSON
curl -sS "$BRIDGE_URL/api/v1/jobs/$JOB/results" -H "Authorization: Bearer $BRIDGE_API_KEY"
# CSV (e.g. to load into the database)
curl -sS "$BRIDGE_URL/api/v1/jobs/$JOB/results?format=csv" -H "Authorization: Bearer $BRIDGE_API_KEY" -o results.csv
```

Query parameters: `format=json|csv` (default json) and `include_unannotated=true` (also
return tasks nobody annotated, with empty answer columns).

Results can be fetched at any time (partial results while annotation is ongoing). There is
**one row per annotation**: if two people annotated the same video, you get two rows.

| Column | Meaning |
|---|---|
| `project_id`, `task_id` | Label Studio ids |
| `media_id`, `prediction_id`, `file` | from your CSV: **join keys back to the database** |
| `model_prediction`, `model_confidence` | the model's guess you sent |
| `annotation_id`, `annotator_id` | Label Studio annotation and user ids |
| `created_at`, `updated_at`, `lead_time_s` | annotation timestamps and seconds spent |
| `was_cancelled` | `true` if the annotator skipped the task |
| `ground_truth` | `true` if marked as ground truth in Label Studio |
| `species_label`, `count`, `certainty`, `day_night`, `age`, `sex`, `recurring_animal`, `leashed`, `horse_with_human`, `notes` | the annotator's answers (one column per question of the labeling UI; several choices are joined with `"; "`) |

Projects not created by the bridge (e.g. the original "wildcam" project) are available
through `GET /api/v1/projects/{project_id}/results` and `/progress`.

## 7. Python client and command line

The repository includes a small client (`pip install` the repo, or copy
`src/wildcam_bridge/client.py`, which only needs `httpx`):

```python
import os
from wildcam_bridge.client import BridgeClient

with BridgeClient(os.environ["BRIDGE_URL"], os.environ["BRIDGE_API_KEY"]) as bridge:
    job = bridge.submit("query.csv", title="Hoestemark batch 1")
    job = bridge.wait(job["id"])                 # polls until ready / failed
    print(job["status"], job["project_url"])
    # ... later, when bridge.progress(job["id"])["complete"] is True:
    rows = bridge.results(job["id"])["rows"]
```

Command line equivalents (they use `BRIDGE_URL` and `BRIDGE_API_KEY`):

```bash
wildcam-bridge client submit query.csv --title "Hoestemark batch 1" --wait
wildcam-bridge client submit query.csv --dry-run --wait        # only check
wildcam-bridge client status 20261112T140501Z-3fa9c1            # status + annotation progress
wildcam-bridge client results 20261112T140501Z-3fa9c1 --format csv -o results.csv
```

## 8. Errors

| HTTP | When |
|---|---|
| 400 | invalid CSV (message says which line/column) |
| 401 | missing or invalid API key |
| 403 | request did not come over HTTPS |
| 404 | unknown job or Label Studio project |
| 409 | results/progress requested for a job that is not `ready`, or retry of a job that did not fail |
| 413 | CSV larger than 20 MB |
| 429 | rate limit exceeded |
| 502 | Label Studio unreachable or rejected the request |

Typical `failed` job errors:

* `N of M file(s) not found on ERDA`: check the `file` paths (see `report.dry_run.missing_sample`),
  or resubmit with `allow_missing=true`.
* `not enough disk space`: ask the administrator to free space on the server.
* `ERDA host key does not match the pinned key`: ERDA changed its SSH key. The administrator
  must verify it (see the dev guide).
* `dry run failed: cannot connect`: ERDA is down, or the sharelink was revoked.
