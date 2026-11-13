# 07: Production pipeline test with `test_query.csv`

**Date:** 2026-11-12 · **Status:** done ✅

## Goal
Run the whole chain on the real systems: `resources/test_query.csv` → bridge API → real
ERDA → production Label Studio → annotation → results. The project owner approved a
clearly named test project in production LS, to be deleted afterwards.

## Service account
The owner created the LS service account and its Personal Access Token (stored in `.env`
only). Question raised: *"I cannot create a mail address for a bot"*. No mailbox is needed:
LS on this server sends no email (`EMAIL_BACKEND` = Django dummy backend), so the address is
only a login name (e.g. `bridge-bot@wildcam.ecoscience.dk`). The dev guide now says so.
`wildcam-bridge check` → all PASS (LS user id 4).

## Rehearsal (throw-away LS, real ERDA, real bridge server on :8091)
94/94 videos (1,364,296,450 B) downloaded in ~37 s, 94 tasks, video served byte-identical,
3 simulated annotations returned as JSON/CSV. Scratch data deleted afterwards.

## Production run (bridge server on :8090 with the production `.env`)
| Step | Result |
|---|---|
| dry-run job `20261112T140449Z-dc6339` | `dry_run_ok`: 94/94 found, 1.36 GB to download, 72.6 GB free |
| full job `20261112T140452Z-3a357f` | `ready` in **76 s**: 94 downloaded, 0 failed, no `.part` left |
| media | `~/labelstudio-data/bridge/20261112T140452Z-3a357f/` (1.3 GB, 94 mp4 + `tasks.json`) |
| LS project | **id 4** "[TEST] wildcam-bridge e2e 2026-11-12", 94 tasks, `model_version=dfbsmdr-01.04.00`, pre-annotations shown |
| video streaming | `/data/local-files/?d=bridge/<job>/data/...` → 200 `video/mp4`, bytes identical |
| annotator (simulated, 3 tasks) | annotations created as the service account |
| `GET /jobs/{id}/progress` | `total_tasks 94, finished_tasks 3, complete false` |
| `GET /jobs/{id}/results?format=csv` | header + 3 rows, joined on `media_id`/`prediction_id` |

Production LS stayed healthy throughout (`/health` 200, `wildcam.service` active).

## Bug found on the way
Under the real uvicorn server (`--root-path /bridge`), `/healthz` was refused without
`X-Forwarded-Proto` because uvicorn includes the root path in `scope["path"]`. Fixed in
commit b11cd5f; the regression test drives the app the way uvicorn does.

## To do (owner)
* ~~Inspect, then **delete LS project 4** and `rm -r ~/labelstudio-data/bridge/20261112T140452Z-3a357f`~~: done 2026-11-13 (entry 10).
* ~~`sudo deploy/install.sh`~~: done by the owner at 14:14 UTC (see entry 08).
