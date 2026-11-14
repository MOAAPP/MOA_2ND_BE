#!/usr/bin/env bash
# One-time install on Ubuntu 26.04 EC2. No extra SSH inbound rule or AWS key.
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then
  echo "Use sudo bash scripts/install-ec2-auto-deploy.sh"
  exit 1
fi
REPO=/home/ubuntu/MOA_2ND_BE
if [[ ! -d "$REPO/.git" || ! -f "$REPO/scripts/auto-deploy-ec2.sh" ]]; then
  echo "Cannot find backend repo at $REPO"
  exit 1
fi

cat >/etc/systemd/system/moa-ec2-auto-deploy.service <<'SERVICE'
[Unit]
Description=MOA FastAPI: deploy only CI-verified GitHub main
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/MOA_2ND_BE
ExecStart=/usr/bin/bash /home/ubuntu/MOA_2ND_BE/scripts/auto-deploy-ec2.sh
TimeoutStartSec=1200
UMask=0077
SERVICE

cat >/etc/systemd/system/moa-ec2-auto-deploy.timer <<'TIMER'
[Unit]
Description=Check CI-verified MOA backend release every five minutes

[Timer]
OnBootSec=3min
OnUnitInactiveSec=5min
RandomizedDelaySec=30sec
Persistent=true
Unit=moa-ec2-auto-deploy.service

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
systemctl enable --now moa-ec2-auto-deploy.timer
echo "Timer enabled. GitHub main + passing FastAPI tests => automatic Docker update."
systemctl list-timers --all moa-ec2-auto-deploy.timer --no-pager
