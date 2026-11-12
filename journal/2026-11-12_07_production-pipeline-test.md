# 07: Production pipeline test with `test_query.csv`

**Date:** 2026-11-12 · **Status:** waiting for the Label Studio service-account token

## Goal
Run the whole chain on the real systems: `resources/test_query.csv` → bridge API → real
ERDA (94 videos, 1.36 GB) → production Label Studio project `[TEST] …` → annotation →
results. Approved by the project owner (test project in production LS, to be deleted
afterwards).

## Done so far
* Real ERDA dry run: OK (see entry 02).
* Full chain on a throw-away Label Studio with a fake SFTP server: OK (entry 05).

## Blocking
`LS_API_TOKEN` in `.env` is empty. The owner creates the `bridge-bot` service account and its
Personal Access Token (dev guide §2, step 2) and pastes the token into `.env`.

## Procedure (once the token is set)
```bash
uv run wildcam-bridge check
uv run wildcam-bridge serve &       # or the systemd service
curl -H "Authorization: Bearer $KEY" -H "X-Forwarded-Proto: https" \
     -F file=@$HOME/resources/test_query.csv -F "title=[TEST] wildcam-bridge e2e 2026-11-12" \
     http://127.0.0.1:8090/api/v1/jobs
```
