#!/usr/bin/env bash
# Menu Watch installer for an Ubuntu/Debian or Amazon Linux server.
#
#   tar xzf menuwatch-deploy.tar.gz && sudo bash menuwatch/deploy/install.sh
#
# Safe to re-run for updates: code and libraries are refreshed, while the database,
# zone settings (config.json) and login password are kept.
#
# Optional overrides:  MENUWATCH_DOMAIN=demo.superagentk.com  MENUWATCH_PORT=8050
set -euo pipefail

DOMAIN="${MENUWATCH_DOMAIN:-demo.superagentk.com}"
PORT="${MENUWATCH_PORT:-8050}"
APP_DIR=/opt/menuwatch
DATA_DIR=/var/lib/menuwatch
ENV_FILE=/etc/menuwatch.env
SERVICE=menuwatch
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
has_systemd() { [ -d /run/systemd/system ]; }

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo bash $0"; exit 1; }
[ -f "$SRC_DIR/wsgi.py" ] || { echo "Can't find the app next to this script ($SRC_DIR)"; exit 1; }

# ---------------------------------------------------------------- 1. system packages
log "Installing system packages"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip libglib2.0-0 libgomp1 curl iproute2 >/dev/null
  PY=python3
elif command -v dnf >/dev/null; then              # Amazon Linux 2023 / RHEL
  dnf install -y -q python3.11 python3.11-pip glib2 libgomp curl-minimal iproute >/dev/null || \
  dnf install -y -q python3.11 python3.11-pip glib2 libgomp iproute >/dev/null
  PY=python3.11
else
  echo "Unsupported Linux distribution (need apt or dnf)."; exit 1
fi
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "Python 3.10+ is required."; exit 1; }

# ---------------------------------------------------------------- 2. app files
log "Copying app to $APP_DIR"
id "$SERVICE" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell "$(command -v nologin || echo /bin/false)" "$SERVICE"
mkdir -p "$APP_DIR" "$DATA_DIR"
rm -rf "$APP_DIR/menu_watch" "$APP_DIR/deploy"
cp -r "$SRC_DIR/menu_watch" "$SRC_DIR/deploy" "$SRC_DIR/footage" "$APP_DIR/"
cp "$SRC_DIR/run.py" "$SRC_DIR/wsgi.py" "$SRC_DIR/yolo11n.pt" "$APP_DIR/"

# Size the analysis to this server. Zones/thresholds in an existing config.json are kept.
# Threads: measured 6.7 fps with 2 threads vs 0.3 fps with 13 (threads fighting each other),
# so use half the cores, max 4. A 2-core server gets 1 thread and keeps a core free for the website.
"$PY" - "$SRC_DIR/config.json" "$DATA_DIR/config.json" "$(nproc)" <<'EOF'
import json, os, sys
src, dst, cores = sys.argv[1], sys.argv[2], int(sys.argv[3])
first_install = not os.path.exists(dst)
cfg = json.load(open(src if first_install else dst))
cfg["model"] = "/opt/menuwatch/yolo11n.pt"
cfg["cpu_threads"] = min(4, max(1, cores // 2))
if first_install and cores <= 2:
    cfg["analysis_fps"] = 5
json.dump(cfg, open(dst, "w"), indent=2)
print(f"  {cores} CPU cores -> {cfg['cpu_threads']} analysis thread(s), {cfg['analysis_fps']} frames/s")
EOF

# ---------------------------------------------------------------- 3. python libraries
log "Installing Python libraries (first run downloads ~400 MB, takes a few minutes)"
[ -x "$APP_DIR/venv/bin/python" ] || "$PY" -m venv "$APP_DIR/venv"
PIP="$APP_DIR/venv/bin/pip"
"$PIP" install -q --upgrade pip
# CPU-only PyTorch first; the default Linux build drags in 2 GB+ of NVIDIA libraries we don't need
"$PIP" install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
"$PIP" install -q -r "$APP_DIR/deploy/requirements-server.txt"
# ultralytics installs the desktop OpenCV build, which needs a display stack; use the headless build.
# Both share the same cv2 folder, so remove one and force-reinstall the other.
"$PIP" uninstall -y -q opencv-python >/dev/null 2>&1 || true
"$PIP" install -q --force-reinstall --no-deps opencv-python-headless

# fresh install: fill two weeks of sample history so the demo dashboard isn't empty
# (remove later with: sudo -u menuwatch /opt/menuwatch/venv/bin/python /opt/menuwatch/seed_demo.py --db /var/lib/menuwatch/menu_watch.db --camera-id demo-cam --remove)
if [ ! -f "$DATA_DIR/menu_watch.db" ]; then
  log "Adding sample history to the new database"
  (cd "$APP_DIR" && "$APP_DIR/venv/bin/python" seed_demo.py --db "$DATA_DIR/menu_watch.db" --camera-id demo-cam)
fi

chown -R "$SERVICE:$SERVICE" "$DATA_DIR"

# ---------------------------------------------------------------- 4. settings + login
NEW_PASSWORD=""
if [ ! -f "$ENV_FILE" ]; then
  log "Creating $ENV_FILE with a new login password"
  NEW_PASSWORD=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(15))')
  cat > "$ENV_FILE" <<EOF
# Menu Watch settings. After editing:  sudo systemctl restart $SERVICE
MENUWATCH_SOURCE=$APP_DIR/footage/shopfront_bradford.mp4
MENUWATCH_LOOP=1
# Demo mode: pause the video analysis when nobody has had the dashboard open for 60 s.
# Remove this line when a real camera is connected so it records all day.
MENUWATCH_IDLE_PAUSE_SEC=60
MENUWATCH_CAMERA_ID=demo-cam
MENUWATCH_CONFIG=$DATA_DIR/config.json
MENUWATCH_DB=$DATA_DIR/menu_watch.db
MENUWATCH_USER=admin
MENUWATCH_PASSWORD=$NEW_PASSWORD
YOLO_CONFIG_DIR=$DATA_DIR/ultralytics
YOLO_OFFLINE=1
EOF
  chmod 600 "$ENV_FILE"
fi

# ---------------------------------------------------------------- 5. service
GUNICORN="$APP_DIR/venv/bin/gunicorn --workers 1 --threads 16 --worker-class gthread --bind 127.0.0.1:$PORT --timeout 120 wsgi:app"
if has_systemd; then
  log "Installing systemd service '$SERVICE'"
  cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=Menu Watch - menu reader counter
After=network-online.target

[Service]
User=$SERVICE
Group=$SERVICE
EnvironmentFile=$ENV_FILE
WorkingDirectory=$APP_DIR
# ONE worker only: the video analyzer runs inside the worker process
ExecStart=$GUNICORN
Restart=always
RestartSec=5
# Be a good neighbour to the website on this server
Nice=10
MemoryMax=2G

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q $SERVICE
  systemctl restart $SERVICE
else
  warn "systemd not found (container?) - starting in the background instead"
  pkill -f "gunicorn.*wsgi:app" 2>/dev/null || true
  ( set -a; . "$ENV_FILE"; set +a; cd "$APP_DIR"
    nohup runuser -u "$SERVICE" -- $GUNICORN >"$DATA_DIR/menuwatch.log" 2>&1 & )
fi

log "Waiting for the app to start (loading the AI model)"
PW=$(grep '^MENUWATCH_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
for _ in $(seq 1 90); do
  if curl -fs -u "admin:$PW" "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then OK=1; break; fi
  sleep 2
done
if [ "${OK:-0}" != 1 ]; then
  warn "The app did not answer on port $PORT. Check the log:"
  has_systemd && echo "  sudo journalctl -u $SERVICE -n 50 --no-pager" || echo "  cat $DATA_DIR/menuwatch.log"
  exit 1
fi
echo "App is running on 127.0.0.1:$PORT"

# ---------------------------------------------------------------- 6. web server (https://DOMAIN/menuwatch/)
reload_nginx() {
  if has_systemd; then systemctl enable -q --now nginx && systemctl reload nginx
  else nginx -s reload 2>/dev/null || nginx; fi
}
write_nginx_snippet() {
  mkdir -p /etc/nginx/snippets
  cat > /etc/nginx/snippets/menuwatch.conf <<EOF
# Menu Watch dashboard at /menuwatch/  (installed by menuwatch/deploy/install.sh)
location /menuwatch/ {
    proxy_pass http://127.0.0.1:$PORT/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_buffering off;        # required for the live video stream
    proxy_read_timeout 1h;
}
location = /menuwatch {
    absolute_redirect off;      # relative redirect keeps https:// behind Cloudflare
    return 301 /menuwatch/;
}
EOF
}

WEB_OK=0
PORT80=$(ss -ltnpH 'sport = :80' 2>/dev/null || true)
if command -v nginx >/dev/null; then
  log "Found nginx - adding /menuwatch/ to the $DOMAIN site"
  write_nginx_snippet
  DOMAIN_RE="${DOMAIN//./\\.}"
  CONF_FILES=$(grep -RlE "server_name[^;]*[[:space:]]$DOMAIN_RE([[:space:];]|$)" /etc/nginx 2>/dev/null \
               | xargs -r readlink -f | sort -u | grep -v snippets/menuwatch.conf || true)
  if [ -n "$CONF_FILES" ]; then
    # backups live outside /etc/nginx so nginx never loads them as extra sites
    BACKUP_DIR=/var/backups/menuwatch-nginx/$(date +%Y%m%d-%H%M%S)
    CHANGED=""
    for real in $CONF_FILES; do
      if ! grep -q "snippets/menuwatch.conf" "$real"; then
        mkdir -p "$BACKUP_DIR$(dirname "$real")"
        cp -p "$real" "$BACKUP_DIR$real"
        sed -i -E "/server_name[^;]*[[:space:]]$DOMAIN_RE([[:space:];]|\$)/a\\    include snippets/menuwatch.conf;" "$real"
        CHANGED="$CHANGED $real"
        echo "  updated $real (backup in $BACKUP_DIR)"
      fi
    done
    if nginx -t 2>/dev/null; then
      reload_nginx; WEB_OK=1
    else
      warn "nginx rejected the change - restoring the original config"
      for real in $CHANGED; do cp -p "$BACKUP_DIR$real" "$real"; done
      nginx -t || true
    fi
  else
    warn "nginx has no server block for $DOMAIN."
    echo "  Add this line inside the server { } block that serves $DOMAIN, then: sudo nginx -t && sudo systemctl reload nginx"
    echo "      include snippets/menuwatch.conf;"
  fi
elif [ -z "$PORT80" ]; then
  log "No web server found - installing nginx for $DOMAIN"
  if command -v apt-get >/dev/null; then apt-get install -y -qq nginx >/dev/null; SITE=/etc/nginx/sites-available/menuwatch
  else dnf install -y -q nginx >/dev/null; SITE=/etc/nginx/conf.d/menuwatch.conf; fi
  write_nginx_snippet
  cat > "$SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location = / { return 302 /menuwatch/; }
    include snippets/menuwatch.conf;
}
EOF
  [ -d /etc/nginx/sites-enabled ] && ln -sf "$SITE" /etc/nginx/sites-enabled/menuwatch
  nginx -t && reload_nginx && WEB_OK=1
else
  warn "Port 80 is used by a web server this script doesn't manage:"
  echo "  $PORT80"
  echo "  Point https://$DOMAIN/menuwatch/ at http://127.0.0.1:$PORT/ yourself - see DEPLOY.md for Caddy/Apache examples."
fi

# ---------------------------------------------------------------- done
log "Done"
echo "  Dashboard : https://$DOMAIN/menuwatch/"
echo "  Username  : admin"
if [ -n "$NEW_PASSWORD" ]; then
  echo "  Password  : $NEW_PASSWORD      <- save this now; it's also in $ENV_FILE (root only)"
else
  echo "  Password  : unchanged (see: sudo grep PASSWORD $ENV_FILE)"
fi
[ "$WEB_OK" = 1 ] || warn "Web server not wired up automatically - see the messages above."
echo
echo "  Status    : sudo systemctl status $SERVICE"
echo "  Live log  : sudo journalctl -u $SERVICE -f"
echo "  Settings  : sudo nano $ENV_FILE   then   sudo systemctl restart $SERVICE"
