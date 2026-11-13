# Journal

One file per task: `YYYY-MM-DD_NN_short-title.md`. Add a row when you add an entry.

| # | Entry | Date | Description |
|---|---|---|---|
| 01 | [Environment discovery](2026-11-12_01_environment-discovery.md) | 2026-11-12 | Inventory of server, Label Studio 1.23, nginx/TLS, resources, legacy scripts, CSV |
| 02 | [ERDA access and dry run](2026-11-12_02_erda-access-and-dry-run.md) | 2026-11-12 | ERDA host key changed → verified and pinned; sharelink layout; first real dry run (94/94, 1.36 GB) |
| 03 | [Repository and tooling](2026-11-12_03_repository-and-tooling.md) | 2026-11-12 | uv project, ruff, mypy --strict, pytest, pre-commit, secret scan, CI |
| 04 | [Bridge implementation](2026-11-12_04_bridge-implementation.md) | 2026-11-12 | FastAPI API, auth, job worker, dry run, SFTP download, Label Studio client, results |
| 05 | [Testing](2026-11-12_05_testing.md) | 2026-11-12 | Unit, integration (fake SFTP), e2e with a throw-away Label Studio |
| 06 | [Deployment and docs](2026-11-12_06_deployment-and-docs.md) | 2026-11-12 | Hardened systemd unit, nginx snippet, install script, user and dev guides |
| 07 | [Production pipeline test](2026-11-12_07_production-pipeline-test.md) | 2026-11-12 | `test_query.csv` → real ERDA → production LS (project 4): 94/94 videos in 76 s, results via API ✅ |
| 08 | [Deployment check and pre-fill](2026-11-12_08_deployment-check-and-prefill.md) | 2026-11-12 | Installed service verified over public HTTPS (TLS 1.3, auth, rate limit, sandbox); model prediction pre-fill verified in LS 1.23 and hardened |
| 09 | [Pre-fill test, full CSV](2026-11-12_09_prefill-test-full-csv.md) | 2026-11-12 | Full `test_query.csv` via public API (project 6): 94/94 videos pre-filled exactly as the CSV; new `scripts/verify_prefill.py` |
| 10 | [Project deletion](2026-11-13_10_project-deletion.md) | 2026-11-13 | `DELETE /jobs/{id}` and `/projects/{id}` (bridge projects only, `force` for annotated ones); test projects 4–6 deleted, 2.7 GB freed |
| 11 | [gen-api-key writes .env](2026-11-13_11_gen-api-key-writes-env.md) | 2026-11-13 | `gen-api-key --name` appends the hash to `.env` (atomic, verified with the service's parser), prints key + restart command |
| 12 | [GitHub remote](2026-11-13_12_github-remote.md) | 2026-11-13 | History checked for secrets, pushed `main` to `GuillaumeMougeot/wildcam-ls-bridge`; CI reproduced on a fresh clone (116 passed) |
