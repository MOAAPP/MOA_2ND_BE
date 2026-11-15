"""Optional production gateway authentication and bounded AI demo quotas.

When MOA_REQUIRE_GATEWAY=1, missing configuration fails closed. The token is
server-only: Vercel Functions send it; VITE_* variables must never contain it.
The SQLite quota database must live on a persistent mounted Docker volume.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

HEADER = "x-moa-gateway-key"
PUBLIC = {"/api/v4/health"}


def _is_paid_candidate(path: str, method: str) -> bool:
    if path == "/api/v4/agent/analyze" and method == "POST":
        return True
    if path == "/api/v4/vision/analyze" and method == "POST":
        return True
    if path == "/api/v4/sms/analyze" and method == "POST":
        return True
    if path.startswith("/api/v4/users/"):
        if path.endswith("/briefing") and method == "GET":
            return True
        if path.endswith("/daily-quiz") and method == "GET":
            return True
    return False


def _limit(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, 1), 10000)


def _allow_paid_request(client: str) -> bool:
    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H")
    day = now.strftime("%Y-%m-%d")
    ip_hash = hashlib.sha256(client.encode("utf-8")).hexdigest()[:32]
    groups = (
        ("global", day, _limit("MOA_AI_DAILY_MAX_REQUESTS", 100)),
        ("client:" + ip_hash, hour, _limit("MOA_AI_PER_CLIENT_HOURLY_MAX", 12)),
    )
    db = Path(os.getenv("MOA_GATEWAY_QUOTA_DB", "/tmp/moa-gateway-quotas.sqlite3"))
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db, timeout=5, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quotas (
                identity TEXT NOT NULL,
                period TEXT NOT NULL,
                requests INTEGER NOT NULL,
                PRIMARY KEY (identity, period)
            )
        """)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for identity, period, ceiling in groups:
                row = conn.execute(
                    "SELECT requests FROM quotas WHERE identity=? AND period=?",
                    (identity, period),
                ).fetchone()
                if row and row[0] >= ceiling:
                    conn.execute("ROLLBACK")
                    return False
            for identity, period, _ in groups:
                conn.execute("""
                    INSERT INTO quotas (identity, period, requests)
                    VALUES (?, ?, 1)
                    ON CONFLICT(identity, period)
                    DO UPDATE SET requests = requests + 1
                """, (identity, period))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


async def gateway_guard(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/v4/") or path in PUBLIC:
        return await call_next(request)

    require = os.getenv("MOA_REQUIRE_GATEWAY", "").strip().lower() in ("1", "true", "yes")
    secret = os.getenv("MOA_GATEWAY_SECRET", "").strip()
    if require and not secret:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "gateway_not_configured",
                                "message": "API 접근 게이트웨이가 설정되지 않았습니다."}},
            headers={"Cache-Control": "no-store"},
        )
    if secret:
        received = request.headers.get(HEADER, "")
        if not hmac.compare_digest(received, secret):
            return JSONResponse(
                status_code=401,
                content={"detail": {"code": "gateway_unauthorized",
                                    "message": "보호된 API입니다."}},
                headers={"Cache-Control": "no-store"},
            )
        if _is_paid_candidate(path, request.method):
            # Only accepted after authenticating the proxy token. A forged IP can
            # evade its personal bucket but never the global daily ceiling.
            client = (request.headers.get("x-moa-client-ip") or
                      (request.client.host if request.client else "unknown"))[:128]
            try:
                allowed = _allow_paid_request(client)
            except (OSError, sqlite3.Error):
                return JSONResponse(
                    status_code=503,
                    content={"detail": {"code": "quota_storage_unavailable",
                                        "message": "AI 요청 보호 기능을 확인할 수 없습니다."}},
                )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": {"code": "ai_demo_quota_reached",
                                        "message": "공모전 시연용 AI 호출 한도에 도달했습니다."}},
                    headers={"Retry-After": "3600", "Cache-Control": "no-store"},
                )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response
