# AU ERDA access

[ERDA](https://erda.au.dk) is Aarhus University's research data store. The wildcam videos
are read from it over **SFTP** (SSH, so encrypted in transit).

## Connection facts (verified 2026-11-12)

| Item | Value |
|---|---|
| Host / port | `io.erda.au.dk` : `2222` (IP 185.45.21.27, reverse DNS `io.erda.au.dk`) |
| Login | **sharelink**: the sharelink id is both SFTP *username* and *password* |
| Secret storage | `ERDA_SHARELINK` in `.env` only |
| Share root | the ERDA `wildcams` folder. Top level: `.upload-cache/`, `Data_backup/`, `Test_data/`, `Trash/`, `data/`, `models/` |
| `statvfs` | not supported by ERDA (remote free space cannot be queried; not needed) |

### Why a sharelink and not a personal SFTP account

A sharelink is not tied to a person's ERDA account, so the pipeline keeps working when people
leave. The share owner can revoke or replace it from the ERDA web UI. After rotating it,
update `ERDA_SHARELINK` in `.env` and restart the service.

### Host key pinning

The legacy scripts used `known_hosts=None` (any server accepted, so man-in-the-middle was
possible). The bridge only connects when the server key matches `config/erda_known_hosts`.

| Date | Key | SHA256 fingerprint | Status |
|---|---|---|---|
| ≤ 2026-05-19 | ssh-rsa 4096 | `51iHGJx1Oav75JV/5csbHBhBQDhDCrW8s5pehstcqLg` | old (in `~/.ssh/known_hosts`) |
| 2026-11-12 | ssh-rsa 4096 | `GNDJeo4be1b3C+VsFDPgg+PdK36ZmU4lf7ZWGvOaMVI` | **pinned** (consistent over repeated scans; approved by the project owner) |

AU ERDA publishes no SSHFP DNS records (UCPH's `io.erda.dk` does, but that is a different
service with different keys). If ERDA rotates its key again, the bridge fails with
*"ERDA host key does not match the pinned key"*. Verify the new fingerprint with ERDA
support, then re-pin it:

```bash
ssh-keyscan -p 2222 io.erda.au.dk > /tmp/erda.pub && ssh-keygen -lf /tmp/erda.pub   # compare!
cp /tmp/erda.pub config/erda_known_hosts && git commit -am "Re-pin ERDA host key"
```

## Mapping the SQL `file` column to ERDA

The SQL database stores `wildcams/data/<site>/<location>/<deployment>/<file>.mp4`.
The sharelink root *is* the `wildcams` folder, so the bridge strips `wildcams/`
(`ERDA_STRIP_PREFIX`) and reads `data/<site>/...` relative to the share root
(`ERDA_REMOTE_ROOT=""`). For `resources/test_query.csv` (94 rows), all 94 files resolve,
1.36 GB in total.

## Legacy scripts (kept outside the repo, logic merged into `src/wildcam_bridge/erda.py`)

* **`rsync.py`**: read `Selection.json` (Label Studio tasks); for each task, turned
  `/data/local-files/?d=TestDataset/Selection/<rel>` into remote `Data_backup/<rel>` and local
  `~/labelstudio-data/selection/<rel>`; downloaded with `asyncssh` SFTP, 16 concurrent
  `sftp.get(..., preserve=True)`, skipping files that existed locally.
* **`miss_files.py`**: same mapping; listed the files still missing locally and downloaded
  them sequentially (second pass after a flaky first run).
* Weaknesses fixed in the bridge: sharelink hard-coded in code, no host-key check, "exists"
  instead of "same size" (truncated files never repaired), no dry run or disk-space check,
  `FileNotFoundError` never raised by asyncssh (misses showed up as generic errors), no
  atomic writes.

### `Selection.json` (first manual import)

A Label Studio task list with model pre-annotations:

```json
[{"data": {"video": "/data/local-files/?d=TestDataset/Selection/New_recording_with_everything/Lille Vildmose/Høstemark_0001_241217_250416/100_BTCF/IMG_0032.MP4"},
  "predictions": [{"model_version": "deepfaune_ensemble",
                   "result": [{"from_name": "species_label", "to_name": "video", "type": "choices",
                               "value": {"choices": ["bird"]}}]}]}]
```

Those videos come from `Data_backup/New_recording_with_everything/...` (the original card
dumps). The SQL database refers to the curated copy under `data/`.
