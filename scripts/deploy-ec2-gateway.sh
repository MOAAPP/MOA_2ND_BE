#!/usr/bin/env bash
# Run on the Ubuntu EC2 instance from the MOA_2ND_BE repository directory.
# Uses a temporary port 8001 to smoke-test before replacing the old container.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env.gateway ]]; then
  python3 - <<'PY'
from pathlib import Path
from secrets import token_urlsafe
p = Path(".env.gateway")
with p.open("x") as f:
    f.write("MOA_REQUIRE_GATEWAY=1\n")
    f.write("MOA_GATEWAY_SECRET=" + token_urlsafe(48) + "\n")
    f.write("MOA_GATEWAY_QUOTA_DB=/var/lib/moa/ai-quotas.sqlite3\n")
    f.write("MOA_RUNTIME_DIR=/var/lib/moa/runtime\n")
    f.write("MOA_STAGE3_STORAGE_DIR=/var/lib/moa/scenarios\n")
    f.write("MOA_STAGE6_STORAGE_DIR=/var/lib/moa/stage6\n")
    f.write("MOA_AI_DAILY_MAX_REQUESTS=100\n")
    f.write("MOA_AI_PER_CLIENT_HOURLY_MAX=12\n")
    f.write("MOA_AI_BASE_URL=https://api.openai.com/v1\n")
    f.write("MOA_AI_MODEL=gpt-4.1-mini\n")
    f.write("MOA_AGENT_MODEL=gpt-4.1-mini\n")
    f.write("MOA_VISION_MODEL=gpt-4o-mini\n")
p.chmod(0o600)
print("Created a private .env.gateway. Never commit or share it.")
PY
fi

sudo docker build -t moa-backend:gateway .
sudo docker volume create moa-persist >/dev/null
sudo docker rm -f moa-backend-verify >/dev/null 2>&1 || true
sudo docker run -d \
  --name moa-backend-verify \
  --env-file "$(pwd)/.env.gateway" \
  -v moa-persist:/var/lib/moa \
  -p 127.0.0.1:8001:8000 \
  moa-backend:gateway >/dev/null

cleanup() { sudo docker rm -f moa-backend-verify >/dev/null 2>&1 || true; }
trap cleanup EXIT

for i in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:8001/api/v4/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

python3 - <<'PY'
from pathlib import Path
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
secret = dict(line.split("=", 1) for line in
              Path(".env.gateway").read_text().splitlines() if "=" in line)["MOA_GATEWAY_SECRET"]
root = "http://127.0.0.1:8001"
def hit(path, key=None):
    headers = {"X-MOA-Gateway-Key": key} if key else {}
    try:
        with urlopen(Request(root + path, headers=headers), timeout=25) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
assert hit("/api/v4/health") == 200
assert hit("/api/v4/users") == 401
assert hit("/api/v4/users", secret) == 200
print("PASS: health=200, direct API=401, authenticated API=200")
PY

# The new container has passed isolation tests. Now swap port 8000.
sudo docker rm -f moa-backend-verify >/dev/null
trap - EXIT

if sudo docker container inspect moa-backend >/dev/null 2>&1; then
  sudo docker stop moa-backend >/dev/null
  sudo docker rm moa-backend >/dev/null
fi
sudo docker run -d \
  --name moa-backend \
  --restart unless-stopped \
  --env-file "$(pwd)/.env.gateway" \
  -v moa-persist:/var/lib/moa \
  -p 127.0.0.1:8000:8000 \
  moa-backend:gateway >/dev/null

curl -fsS http://127.0.0.1:8000/api/v4/health
printf '\nGateway installed. Next: put the same MOA_GATEWAY_SECRET into Vercel as MOA_PROXY_SHARED_KEY.\n'
printf 'To read the token locally (do NOT paste in ChatGPT):\n'
printf "  sed -n 's/^MOA_GATEWAY_SECRET=//p' .env.gateway\n"
printf 'If user scenario data existed inside the old container, this replacement does not migrate it.\n'
