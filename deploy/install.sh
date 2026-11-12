#!/usr/bin/env bash
# Install the bridge as a systemd service and publish it through nginx at /bridge/.
#
#   sudo deploy/install.sh               # defaults match the wildcam.ecoscience.dk server
#   sudo NGINX_SITE=/etc/nginx/sites-available/mysite deploy/install.sh
#
# Idempotent. The nginx site file is backed up and restored automatically if `nginx -t` fails.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${RUN_USER:-$(stat -c %U "$REPO")}"
NGINX_SITE="${NGINX_SITE:-/etc/nginx/sites-available/wildcam}"
MEDIA="${MEDIA:-$(grep -E '^LS_DATA_ROOT=' "$REPO/.env" | cut -d= -f2 | awk '{print $1}')/$(grep -E '^LS_DATA_SUBDIR=' "$REPO/.env" | cut -d= -f2 | awk '{print $1}')}"
SNIPPET=/etc/nginx/snippets/wildcam-bridge.conf
INCLUDE_LINE="    include ${SNIPPET};"

[[ $(id -u) -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -f "$REPO/.env" ]] || { echo "$REPO/.env missing (cp .env.example .env)" >&2; exit 1; }
[[ -x "$REPO/.venv/bin/wildcam-bridge" ]] || { echo "run 'uv sync' as $RUN_USER first" >&2; exit 1; }
[[ -f "$NGINX_SITE" ]] || { echo "nginx site $NGINX_SITE not found" >&2; exit 1; }
[[ "$MEDIA" == /* ]] || { echo "could not derive media folder from .env" >&2; exit 1; }

echo "repo=$REPO user=$RUN_USER media=$MEDIA site=$NGINX_SITE"

# ---- folders the hardened service may write to
sudo -u "$RUN_USER" mkdir -p "$REPO/var" "$MEDIA"
chmod 600 "$REPO/.env"

# ---- systemd
sed -e "s#@REPO@#${REPO}#g" -e "s#@USER@#${RUN_USER}#g" -e "s#@MEDIA@#${MEDIA}#g" \
    "$REPO/deploy/systemd/wildcam-bridge.service" > /etc/systemd/system/wildcam-bridge.service
systemctl daemon-reload
systemctl enable --now wildcam-bridge.service
systemctl restart wildcam-bridge.service

# ---- nginx
install -m 644 "$REPO/deploy/nginx/wildcam-bridge.conf" "$SNIPPET"
install -m 644 "$REPO/deploy/nginx/wildcam-bridge-ratelimit.conf" /etc/nginx/conf.d/wildcam-bridge-ratelimit.conf
if ! grep -qF "$SNIPPET" "$NGINX_SITE"; then
    backup="${NGINX_SITE}.bak.$(date +%Y%m%d%H%M%S)"
    cp -p "$NGINX_SITE" "$backup"
    # insert the include just before the FIRST "listen 443 ssl" line (= the TLS server block)
    awk -v inc="$INCLUDE_LINE" '!done && /^[[:space:]]*listen[[:space:]]+443[[:space:]]+ssl/ {print inc; done=1} {print}' \
        "$backup" > "$NGINX_SITE"
    grep -qF "$SNIPPET" "$NGINX_SITE" || { cp -p "$backup" "$NGINX_SITE"; echo "no 'listen 443 ssl' line found" >&2; exit 1; }
    echo "added include to $NGINX_SITE (backup: $backup)"
fi
if ! nginx -t; then
    [[ -n "${backup:-}" ]] && cp -p "$backup" "$NGINX_SITE" && echo "nginx -t failed: site restored" >&2
    rm -f /etc/nginx/conf.d/wildcam-bridge-ratelimit.conf
    exit 1
fi
systemctl reload nginx

sleep 2
systemctl --no-pager --lines=5 status wildcam-bridge.service || true
echo
echo "Health check (via nginx/TLS):"
curl -fsS "https://$(grep -m1 -oP 'server_name\s+\K[^;]+' "$NGINX_SITE")/bridge/healthz" && echo
