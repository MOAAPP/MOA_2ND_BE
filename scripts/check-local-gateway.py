#!/usr/bin/env python3
"""Smoke-test health, missing-token denial, and authenticated read (no AI charges)."""
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

port = int(sys.argv[1])
env_path = Path(sys.argv[2])
settings = dict(
    line.split("=", 1) for line in env_path.read_text().splitlines()
    if "=" in line and not line.lstrip().startswith("#")
)
secret = settings["MOA_GATEWAY_SECRET"]
base = "http://127.0.0.1:" + str(port)

def status(path, supplied_secret=None):
    headers = {"X-MOA-Gateway-Key": supplied_secret} if supplied_secret else {}
    try:
        with urlopen(Request(base + path, headers=headers), timeout=10) as response:
            return response.status
    except HTTPError as error:
        return error.code

for attempt in range(40):
    try:
        if status("/api/v4/health") == 200:
            break
    except (OSError, URLError):
        pass
    time.sleep(1)
else:
    sys.exit("MOA candidate failed to start.")
assert status("/api/v4/users") == 401, "Anonymous API must be denied"
assert status("/api/v4/users", secret) == 200, "Authorized API must work"
print("PASS: health 200 / anonymous 401 / authorized 200")
