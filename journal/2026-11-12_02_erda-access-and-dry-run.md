# 02: ERDA access, host-key verification, first dry run

**Date:** 2026-11-12 · **Status:** done

## Goal
Verify the ERDA connection exists and the CSV paths resolve, before any download.

## Steps and findings
1. First connection with `~/.ssh/known_hosts` **failed**: ERDA's host key changed since May
   (`SHA256:51iHGJx1…` → `SHA256:GNDJeo4be1b3C+VsFDPgg+PdK36ZmU4lf7ZWGvOaMVI`).
2. Checks: the new key is identical over repeated scans; reverse DNS of 185.45.21.27 is
   `io.erda.au.dk`, in the same /24 as `erda.au.dk`. AU ERDA publishes no SSHFP record
   (`io.erda.dk` = UCPH does; different keys). The key cannot be proven cryptographically,
   so the decision went to the project owner, who **approved pinning the new key**
   (`config/erda_known_hosts`).
3. The project owner confirmed: keep the **sharelink** (not tied to a personal ERDA account).
4. Read-only listing: the share root contains `Data_backup/`, `Test_data/`, `data/`,
   `models/`, … The SQL paths `wildcams/data/...` resolve as `data/...`, so
   `ERDA_STRIP_PREFIX=wildcams/`. The folder holds exactly the 94 CSV files.
5. ERDA does not support `statvfs`.
6. **Dry run** with the bridge CLI:

```
$ uv run wildcam-bridge dry-run ~/resources/test_query.csv
OK: 94/94 files on ERDA, 1.4 GB to download, 72.6 GB free (reserve 10.0 GB).
```
(`total_bytes` = 1,364,296,450; 2.6 s.)

## Decisions
* Host key pinned in git (public data); a mismatch makes the job fail loudly.
* Sharelink only in `.env` (copied from `rsync.py` without printing it).

## Recorded in the repo
[docs/reference/erda.md](../docs/reference/erda.md).
