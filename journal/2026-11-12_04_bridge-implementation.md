# 04: Bridge implementation (API, pipeline, Label Studio client)

**Date:** 2026-11-12 · **Status:** done

## Goal
HTTP API that receives a CSV, downloads the videos from ERDA (with a dry run), creates a
Label Studio project, and later returns the annotations.

## Design
* **FastAPI** app on `127.0.0.1:8090`, published by nginx at `/bridge/` (TLS).
* **Auth:** `Authorization: Bearer <key>`; only SHA-256 hashes are stored
  (`BRIDGE_API_KEY_HASHES`); constant-time comparison. Middleware rejects non-HTTPS requests
  (`X-Forwarded-Proto` from nginx; the app is loopback-only so the header cannot be forged).
* **Jobs:** SQLite (`var/bridge.sqlite3`), one worker thread (sequential, which avoids
  disk-space races). States: queued → checking → downloading → importing → ready (or
  dry_run_ok / failed). Retry endpoint; jobs interrupted by a restart become `failed`.
* **Dry run** (always before downloading): LS token check, ERDA login with pinned key, `stat`
  of every file, free disk ≥ download + `DISK_RESERVE_GB`. Missing files fail the job unless
  `allow_missing`.
* **Download** = `rsync.py` + `miss_files.py` merged: concurrent SFTP gets, skip files that
  are complete (same size), `.part` + size check + atomic rename, retry passes that reconnect
  and halve concurrency.
* **Path safety:** CSV paths are validated (no absolute paths, `..`, backslashes, control
  characters); the resolved local path must stay inside the job folder (symlink-safe).
* **Label Studio:** project with `config/label_config.xml`, a *Local files* storage on the job
  folder (needed for media permission; regex `(?!)` so *Sync* never duplicates), tasks
  imported via API with model pre-annotations, `show_collab_predictions` + `model_version` set.
  The PAT is exchanged for short-lived access tokens (`/api/token/refresh/`).
* **Results:** LS JSON export flattened to one row per annotation, one column per UI control
  (from the project's own label config), JSON or CSV.

## Pitfalls met
* `from __future__ import annotations` + a locally defined `Annotated` alias made FastAPI
  silently ignore the `format` query alias → the alias moved to module level, and a regression
  test checks the OpenAPI parameters.
* FastAPI 0.16x deprecates `FastAPI(root_path=...)` → set on uvicorn instead.
* `JobStore.list` shadowed `list` in annotations (mypy) → renamed `recent`.
* LS 1.23 export lists prediction **ids** only (not objects): irrelevant for results (the model
  guess travels in `data.model_prediction`).
* The template has the typo `procupine`: left unchanged, `porcupine` mapped onto it.
