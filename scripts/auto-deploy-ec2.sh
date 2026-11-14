#!/usr/bin/env bash
# Poll the canonical GitHub main workflow, then deploy only a successful SHA.
# Designed for the current AWS Ubuntu user and existing local-only port 8000.
set -Eeuo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
REPO=/home/ubuntu/MOA_2ND_BE
cd "$REPO"

exec 9>"$HOME/.moa-ec2-auto-deploy.lock"
flock -n 9 || { echo "Another MOA deployment is already running."; exit 0; }

if [[ ! -f .env.gateway ]]; then
  echo "Skipped: .env.gateway is missing. Run bash scripts/deploy-ec2-gateway.sh once."
  exit 0
fi
if ! grep -qx 'MOA_REQUIRE_GATEWAY=1' .env.gateway ||
   ! grep -q '^MOA_GATEWAY_SECRET=.' .env.gateway; then
  echo "Skipped: protected gateway secret configuration is incomplete."
  exit 0
fi
if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "Skipped: local checkout must be on main."
  exit 0
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Skipped: local tracked changes detected; refusing to overwrite them."
  exit 0
fi

approved_sha="$(python3 scripts/ci-approved-sha.py)"
if [[ -z "$approved_sha" ]]; then
  echo "Skipped: latest GitHub main test has not succeeded yet."
  exit 0
fi
git fetch --no-tags --quiet origin main
remote_sha="$(git rev-parse refs/remotes/origin/main)"
if [[ "$remote_sha" != "$approved_sha" ]]; then
  echo "Skipped: latest main SHA has not passed CI."
  exit 0
fi

marker="$HOME/.moa-ec2-deployed-sha"
if [[ -f "$marker" && "$(cat "$marker")" == "$approved_sha" ]]; then
  echo "No changes: $approved_sha"
  exit 0
fi
if ! sudo -n docker info >/dev/null 2>&1; then
  echo "Skipped: ubuntu needs passwordless Docker access through sudo."
  exit 1
fi
if [[ "$(df -Pk / | awk 'NR==2 {print $4}')" -lt 1500000 ]]; then
  echo "Skipped: less than 1.5 GB disk available. Check Docker images."
  exit 1
fi

# Never switch Docker until Git and the new isolated container pass their tests.
git merge --ff-only origin/main
image="moa-backend:$(git rev-parse --short=12 HEAD)"
echo "Building tested MOA release: $(git rev-parse --short=12 HEAD)"
sudo -n docker build -t "$image" .

sudo -n docker volume create moa-persist >/dev/null
sudo -n docker rm -f moa-backend-check >/dev/null 2>&1 || true
sudo -n docker volume rm moa-smoke >/dev/null 2>&1 || true
sudo -n docker volume create moa-smoke >/dev/null
cleanup() {
  sudo -n docker rm -f moa-backend-check >/dev/null 2>&1 || true
  sudo -n docker volume rm moa-smoke >/dev/null 2>&1 || true
}
trap cleanup EXIT

sudo -n docker run -d --name moa-backend-check \
  --env-file "$REPO/.env.gateway" \
  -v moa-smoke:/var/lib/moa \
  -p 127.0.0.1:8001:8000 \
  "$image" >/dev/null

python3 scripts/check-local-gateway.py 8001 "$REPO/.env.gateway"
cleanup
trap - EXIT

if sudo -n docker container inspect moa-backend-rollback >/dev/null 2>&1; then
  echo "Cannot deploy: stale rollback container exists. Inspect it first."
  exit 1
fi

restore_old() {
  echo "New container failed: restoring previous MOA backend."
  sudo -n docker rm -f moa-backend >/dev/null 2>&1 || true
  if sudo -n docker container inspect moa-backend-rollback >/dev/null 2>&1; then
    sudo -n docker rename moa-backend-rollback moa-backend
    sudo -n docker start moa-backend >/dev/null
  fi
  exit 1
}

had_old=0
if sudo -n docker container inspect moa-backend >/dev/null 2>&1; then
  had_old=1
  # Preserve a copy of legacy container-only demo state before first upgrade.
  if sudo -n docker exec moa-backend test -d /app/app/data/runtime 2>/dev/null; then
    backup="$HOME/moa-backups/$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$backup"
    sudo -n docker cp moa-backend:/app/app/data/runtime/. "$backup/" >/dev/null || true
    echo "Legacy demo-state backup location: $backup"
  fi
  sudo -n docker rename moa-backend moa-backend-rollback
  sudo -n docker stop moa-backend-rollback >/dev/null || restore_old
fi

if ! sudo -n docker run -d --name moa-backend --restart unless-stopped \
  --env-file "$REPO/.env.gateway" \
  -v moa-persist:/var/lib/moa \
  -p 127.0.0.1:8000:8000 \
  "$image" >/dev/null; then
  restore_old
fi
if ! python3 scripts/check-local-gateway.py 8000 "$REPO/.env.gateway"; then
  restore_old
fi

if [[ "$had_old" -eq 1 ]]; then
  sudo -n docker rm moa-backend-rollback >/dev/null
fi
printf '%s\n' "$approved_sha" > "$marker"
echo "MOA backend deployed successfully: $approved_sha"
echo "Caddy remains available; public port 8000 was not opened."
