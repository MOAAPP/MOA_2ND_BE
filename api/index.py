"""Vercel ASGI entrypoint for the canonical read-only synthetic FastAPI API.

Only the verified 20-user synthetic ZIP is shipped. Local .env is not shipped.
Transient session files live in /tmp and MUST NOT be described as durable.
"""
import os

os.environ.setdefault("MOA_RUNTIME_DIR", "/tmp/moa-finance/runtime")
os.environ.setdefault("MOA_STAGE3_STORAGE_DIR", "/tmp/moa-finance/runtime/scenarios")
os.environ.setdefault("MOA_STAGE6_STORAGE_DIR", "/tmp/moa-finance/runtime/stage6")

from app.main import app  # noqa: E402
