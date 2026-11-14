#!/usr/bin/env bash
# One-time secret creation. Unlike deploy-ec2-gateway.sh this does not touch Docker.
set -euo pipefail
cd /home/ubuntu/MOA_2ND_BE
umask 077
python3 - <<'PY'
from pathlib import Path
from secrets import token_urlsafe

path = Path(".env.gateway")
if path.exists():
    path.chmod(0o600)
    print("Existing .env.gateway retained unchanged.")
else:
    with path.open("x") as file:
        file.write("MOA_REQUIRE_GATEWAY=1\n")
        file.write("MOA_GATEWAY_SECRET=" + token_urlsafe(48) + "\n")
        file.write("MOA_GATEWAY_QUOTA_DB=/var/lib/moa/ai-quotas.sqlite3\n")
        file.write("MOA_RUNTIME_DIR=/var/lib/moa/runtime\n")
        file.write("MOA_STAGE3_STORAGE_DIR=/var/lib/moa/scenarios\n")
        file.write("MOA_STAGE6_STORAGE_DIR=/var/lib/moa/stage6\n")
        file.write("MOA_AI_DAILY_MAX_REQUESTS=100\n")
        file.write("MOA_AI_PER_CLIENT_HOURLY_MAX=12\n")
        file.write("MOA_AI_BASE_URL=https://api.openai.com/v1\n")
        file.write("MOA_AI_MODEL=gpt-4.1-mini\n")
        file.write("MOA_AGENT_MODEL=gpt-4.1-mini\n")
        file.write("MOA_VISION_MODEL=gpt-4o-mini\n")
    path.chmod(0o600)
    print("Created private .env.gateway. Token was NOT printed.")
print("For optional AI provider credentials, edit the file locally on EC2; never commit it.")
PY
