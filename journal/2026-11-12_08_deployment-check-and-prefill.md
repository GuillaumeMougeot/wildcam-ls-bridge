# 08: Deployment check and model-prediction pre-fill

**Date:** 2026-11-12 · **Status:** done ✅

## Installation (run by the owner)
`sudo deploy/install.sh` at 14:14 UTC: `wildcam-bridge.service` active (0 restarts), the
nginx `include` was inserted just before `listen 443 ssl`, backup
`/etc/nginx/sites-available/wildcam.bak.20261112141400`, health check 200.

## Public endpoint checks (`https://wildcam.ecoscience.dk/bridge`)
| Check | Result |
|---|---|
| `/healthz` | 200 |
| TLS | TLSv1.3 / TLS_AES_256_GCM_SHA384, certificate verified |
| `http://…/bridge/…` | 301 → https |
| HSTS | `max-age=31536000` |
| no / wrong API key | 401 / 401 |
| valid API key | 200 |
| rate limit, 40 simultaneous requests | 19 × 200, 21 × 429 |

## Sandbox checks (hardened systemd unit)
* Dry-run job through the public URL with `wildcam-bridge client submit --dry-run --wait`:
  `20261112T142047Z-bd564d` → `dry_run_ok` (DNS, SSH to ERDA, LS, and writing `var/` all work
  under `ProtectSystem=strict`).
* One-video full job `20261112T142121Z-cb212c` → `ready`, LS **project 5**; the video was
  written to `labelstudio-data/bridge/…` (a `ReadWritePaths` entry), served by LS, prediction
  `human` pre-filled.

## Request: "pre-fill the model prediction from the CSV; if there is none, keep current behaviour"
This was already implemented (CSV `prediction` → LS prediction on `species_label`). I checked
that annotators really see it pre-filled, end to end:
* backend: the task API sends predictions filtered on `project.model_version` (the ML-backend
  feature flag is off in 1.23);
* frontend (`web/dist/apps/labelstudio/167.js`): with no annotation yet and predictions
  present, the form is built by `addAnnotationFromPrediction(...)`, unless pre-annotations are
  "revealed interactively";
* production project 4: all 94 tasks deliver their CSV prediction to the UI.

Hardening: the bridge now also sets `reveal_preannotations_interactively=false` explicitly
(the LS default, but the frontend pre-fill depends on it). New tests: flags sent to LS, a
NULL prediction gives a blank form, a CSV without a `prediction` column gives no
pre-annotations and no warning; e2e checks the flag on a real LS.
The user guide has a new section, *Model predictions are pre-filled*.

The running service picks the change up at its next restart
(`sudo systemctl restart wildcam-bridge`). Nothing breaks before that, because the flag
already has this value by default.

## API keys
One client key exists (`secrets/api-key-sql-client.txt`, hash in `.env`). No other key is
needed for testing.

## Test leftovers to delete when done (owner)
LS projects **4** and **5**, and
`~/labelstudio-data/bridge/20261112T140452Z-3a357f`, `…/20261112T142121Z-cb212c`.
