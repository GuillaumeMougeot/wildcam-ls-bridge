# Server inventory: wildcam.ecoscience.dk (as found 2026-11-12)

Snapshot of the server **before** the bridge was added. Use it to rebuild the server or to
understand what the bridge relies on. Nothing secret is recorded here.

## Machine

| Item | Value |
|---|---|
| OS | Ubuntu, kernel 6.8 (x86_64) |
| Disk | `/dev/vda1` 116 GB, ~48 GB used / ~68 GB free (2026-11-12) |
| Unix user | `wildcam` (sudo requires a password) |
| Python | system 3.12.3; `uv` 0.11 in `~/.local/bin/uv` |

## Label Studio

| Item | Value |
|---|---|
| Version | Label Studio **1.23.0** Community, `label-studio-sdk` 2.0.18 |
| Install | `uv` venv at `/home/wildcam/labelstudio/.venv` (the Windows/conda guide was adapted to uv) |
| Data dir (DB, uploads) | `~/.local/share/label-studio/` (`label_studio.sqlite3`, SQLite) |
| Bind | `127.0.0.1:8081` (never exposed directly) |
| Service | `wildcam.service` (systemd, user `wildcam`) |
| Auth tokens | org setting: Personal Access Tokens **enabled**, legacy tokens **disabled**, PAT TTL 73000 days |
| Media | `LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=/home/wildcam/labelstudio-data` |

`/etc/systemd/system/wildcam.service` (verbatim, no secrets):

```ini
[Unit]
Description=Label Studio
After=network.target

[Service]
Type=simple
User=wildcam
WorkingDirectory=/home/wildcam/labelstudio

Environment=PATH=/home/wildcam/labelstudio/.venv/bin
Environment=CSRF_TRUSTED_ORIGINS=https://wildcam.ecoscience.dk
Environment=LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true
Environment=LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=/home/wildcam/labelstudio-data
Environment=LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true

ExecStart=/home/wildcam/labelstudio/.venv/bin/label-studio start \
    --host 127.0.0.1 \
    --port 8081

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true`: new accounts (e.g. the bridge service
account) can only be created via an invite link from *Organization → Add People*.

## nginx + TLS

* `/etc/nginx/sites-available/wildcam` (enabled), `server_name wildcam.ecoscience.dk`.
* TLS by **certbot** (Let's Encrypt): `/etc/letsencrypt/live/wildcam.ecoscience.dk/`.
* Port 80 block redirects to HTTPS. `client_max_body_size 200M`.
* `location /` proxies to `http://127.0.0.1:8081` with `Host`, `X-Real-IP`,
  `X-Forwarded-For/Proto/Host`, and WebSocket upgrade headers.
* A copy of the site file also lived in `~/wildcam` (home directory).
* The bridge adds `location /bridge/` through `deploy/nginx/wildcam-bridge.conf`
  (included in the TLS block) and a rate-limit zone in `conf.d/`.

## Data folders

| Path | Content |
|---|---|
| `~/labelstudio-data/selection/` | first manual import (~2,700 videos, 41 GB) + `Selection.json`, used by LS project id 2 "wildcam" |
| `~/labelstudio-data/bridge/<job_id>/` | **new**: one folder per bridge job (videos + `tasks.json`) |
| `~/rsync_tmp/filelist_*.txt` | lists of relative video paths used by an earlier rsync attempt |
| `~/Selection.json`, `~/Selection_old.json` | Label Studio task lists of the first import (see [erda.md](erda.md)) |
| `~/rsync.py`, `~/miss_files.py` | legacy download scripts (contain the sharelink in clear text; superseded by the bridge) |
