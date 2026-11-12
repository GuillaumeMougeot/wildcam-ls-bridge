# 01: Environment discovery

**Date:** 2026-11-12 · **Status:** done

## Goal
Understand the existing server, Label Studio installation, data and scripts before building.

## What was inspected
* `~/resources/`: `test_query.csv` (SQL export, 94 rows), `UI_template.txt` (labeling UI),
  `label-studio-guide-wildcam/` (Windows/conda setup guide, 32 screenshots).
* `~/rsync.py`, `~/miss_files.py`: asyncssh SFTP download scripts (sharelink hard-coded).
* `~/Selection.json`: Label Studio task list with `deepfaune_ensemble` predictions.
* `~/labelstudio/.venv`: Label Studio **1.23.0**, SDK 2.0.18, Python 3.12.3 (uv).
* `wildcam.service` (systemd), nginx site `wildcam` + certbot TLS, `~/.bash_history`.
* LS SQLite DB (read-only): 3 users, 1 project ("wildcam", id 2), PAT enabled / legacy
  tokens disabled.
* Disk: 116 GB, 68 GB free; `~/labelstudio-data/selection` holds 41 GB / 2,718 files.

## Findings
* LS listens on `127.0.0.1:8081`, nginx terminates TLS for `wildcam.ecoscience.dk`.
* `LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=/home/wildcam/labelstudio-data`, so new job
  folders must live below it.
* `sudo` needs a password: system changes (systemd, nginx) must be done by the owner →
  provided as `deploy/install.sh`.
* CSV: one row per media (94 media, 94 files), all `video/mp4`, all predictions are valid UI
  choices.
* LS serves `/data/local-files` only when a *Local files* storage path is a string prefix of
  the file's folder (source: `io_storages/localfiles/views.py`).

## Recorded in the repo
[docs/reference/server-inventory.md](../docs/reference/server-inventory.md),
[csv-schema.md](../docs/reference/csv-schema.md),
[label-studio-notes.md](../docs/reference/label-studio-notes.md),
`config/label_config.xml` (copy of the UI template).
