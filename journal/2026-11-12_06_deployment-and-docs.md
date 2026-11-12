# 06: Deployment artefacts and documentation

**Date:** 2026-11-12 · **Status:** done (installation needs sudo, which is the owner's step)

## Deployment
* `deploy/systemd/wildcam-bridge.service`: hardened unit (`ProtectSystem=strict`,
  `ProtectHome=read-only`, writable only `var/` and `labelstudio-data/bridge/`, no
  capabilities).
* `deploy/nginx/wildcam-bridge.conf`: `location /bridge/` → `127.0.0.1:8090` inside the
  existing TLS server block, rate limit 5 r/s (burst 20), HSTS, optional IP allow-list.
* `deploy/install.sh` (sudo): installs both, inserts the `include` before the first
  `listen 443 ssl`, backs up the site and restores it if `nginx -t` fails, then calls
  `/bridge/healthz`.

## Documentation
* `docs/user-guide.md`: API usage (curl, Python client, CLI), job states, result columns, errors.
* `docs/dev-guide.md`: architecture, install on a new server (LS + service account + bridge
  + nginx), configuration, operations (keys, token and host-key rotation, disk cleanup), tests.
* `docs/reference/*`: everything learnt from the resources, scripts and server.
