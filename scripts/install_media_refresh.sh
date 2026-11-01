#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/chzzk-bot
PY=.venv/bin/python
if [ ! -x "$PY" ]; then PY=.venv-test/bin/python; fi
test -x "$PY"
"$PY" -m py_compile collect_creator_media.py fanart_library.py random_media.py
mkdir -p creator_media/fanart_inbox
"$PY" fanart_library.py
cat >/tmp/abyss-media-refresh.service <<EOF
[Unit]
Description=Abyss official images and approved fan-art ingestion
[Service]
Type=oneshot
User=$(whoami)
WorkingDirectory=/home/ubuntu/chzzk-bot
Environment=BOT_DATA_DIR=/home/ubuntu/chzzk-bot
Environment=ABYSS_PUBLIC_X_DISCOVERY=0
ExecStart=/bin/bash -c '$PY collect_creator_media.py; $PY fanart_library.py'
TimeoutStartSec=240
EOF
cat >/tmp/abyss-media-refresh.timer <<'EOF'
[Unit]
Description=Abyss image refresh every six hours
[Timer]
OnCalendar=*-*-* 00,06,12,18:00:00
Persistent=true
RandomizedDelaySec=120
Unit=abyss-media-refresh.service
[Install]
WantedBy=timers.target
EOF
sudo install -m 644 /tmp/abyss-media-refresh.service /etc/systemd/system/abyss-media-refresh.service
sudo install -m 644 /tmp/abyss-media-refresh.timer /etc/systemd/system/abyss-media-refresh.timer
sudo systemctl daemon-reload
sudo systemctl enable --now abyss-media-refresh.timer
sudo systemctl start abyss-media-refresh.service
sudo systemctl is-active abyss-media-refresh.timer
sudo systemctl --no-pager --full status abyss-media-refresh.service | tail -n 10 || true
sudo systemctl show abyss-media-refresh.service -p Result --value | grep -qx success
echo REFRESH_DEPLOY_SUCCESS
