#!/usr/bin/env python3
"""Print the last CI-passed main SHA, or nothing if not yet deployable.

No GitHub token: the MOA backend is a public repository.
Only successful push-to-main runs of the canonical FastAPI test workflow count.
"""
import json
import re
import sys
import urllib.request

URL = (
    "https://api.github.com/repos/MOAAPP/MOA_2ND_BE/actions/"
    "workflows/validate-main.yml/runs?branch=main&event=push&per_page=1"
)
try:
    request = urllib.request.Request(
        URL, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MOA-EC2-CI-Gated-Deploy/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)
    runs = data.get("workflow_runs") or []
    if not runs:
        sys.exit(0)
    run = runs[0]
    sha = run.get("head_sha", "")
    if (
        run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and re.fullmatch("[a-f0-9]{40}", sha)
    ):
        print(sha)
except (OSError, ValueError, KeyError) as error:
    print("GitHub Actions status check unavailable: " + str(error), file=sys.stderr)
    sys.exit(0)
