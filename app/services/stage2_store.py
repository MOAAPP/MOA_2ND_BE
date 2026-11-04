"""Local file overlay. Synthetic source ZIP never receives writes.

This is intentionally a local-development storage layer, NOT a multi-user authenticated
production database. Atomic replace + a process-local lock; run uvicorn with one worker.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.core.settings import BACKEND_DIR

_LOCK = threading.RLock()


def runtime_dir() -> Path:
    return Path(os.getenv("MOA_RUNTIME_DIR", str(BACKEND_DIR / "app/data/runtime"))).resolve()


def _path(user_id: str) -> Path:
    if not re.fullmatch(r"S\d\d", user_id):
        raise ValueError("invalid_user_id")
    root = runtime_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / (user_id + ".json")


def read_user(user_id: str) -> dict:
    with _LOCK:
        path = _path(user_id)
        if not path.exists():
            return {"transactions": [], "analyses": [], "purchase_candidates": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "transactions": data.get("transactions", []),
            "analyses": data.get("analyses", []),
            "purchase_candidates": data.get("purchase_candidates", []),
        }


def mutate_user(user_id: str, mutation):
    with _LOCK:
        data = read_user(user_id)
        result = mutation(data)
        path = _path(user_id)
        temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        return result


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
