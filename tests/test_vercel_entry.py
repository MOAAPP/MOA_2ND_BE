"""Check production ASGI entrypoint and readable canonical data without credentials."""
from fastapi.testclient import TestClient

def test_serverless_asgi_entrypoint():
    from api.index import app
    c = TestClient(app)
    a = c.get("/api/v4/health")
    assert a.status_code == 200, a.text
    assert a.json()["users"] == 20
    assert a.json()["snapshot_date"] == "2026-11-04"
    b = c.get("/api/v4/users/S01/briefing", params={"as_of":"2026-11-04"})
    assert b.status_code == 200, b.text
    assert b.json()["user_id"] == "S01"
    assert b.json()["spending_capacity"]["current_cash"] == 27800
    # The user's API key is NOT in this code; this proves mathematical APIs work
    # without requiring live LLM credentials.
