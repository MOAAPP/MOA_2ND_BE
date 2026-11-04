import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app.main import app
from app.services.dataset import load_dataset
from app.services.stage2_sms import parse_one, mask_sensitive
from app.services.stage2_store import mutate_user, read_user
from app.services.stage2_vision import checkout_total
from app.stage2_models import VisionFields, VisionAnalysis

client = TestClient(app)

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MOA_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

@pytest.mark.parametrize("message,kind,method,has_debit", [
    ("KB국민체크 승인 18,500원 2026/11/05 12:10\n배달의민족", "debit_approval", "debit", True),
    ("신한카드 승인 32,000원 일시불 2026/11/05 19:02\n무신사", "credit_approval", "credit", False),
    ("현대카드 할부 3개월 승인 120,000원 2026/11/05 10:00\n가구점", "installment_approval", "credit", False),
    ("삼성카드 카드대금 출금 123,400원 2026/11/05 09:00", "card_bill", "transfer", True),
    ("우리은행 자동이체 출금 48,000원 2026/11/05 09:00", "automatic_transfer", "transfer", True),
    ("토스뱅크 급여 입금 1,200,000원 2026/11/05 09:00", "salary", "transfer", True),
    ("카카오뱅크 용돈 입금 100,000원 2026/11/05 09:00", "allowance", "transfer", True),
    ("토스뱅크 입금 80,000원 2026/11/05 09:00", "account_in", "transfer", True),
    ("우리은행 출금 20,000원 2026/11/05 09:00", "account_out", "transfer", True),
    ("KB국민체크 결제 취소 10,000원 2026/11/05 09:00", "cancellation", "unknown", False),
    ("삼성카드 환불 10,000원 2026/11/05 09:00", "refund", "unknown", False),
    ("ATM 현금 인출 50,000원 2026/11/05 09:00", "atm_withdrawal", "atm", True),
])
def test_rule_families(message,kind,method,has_debit):
    t = parse_one("S01",message,date(2026,11,5))
    assert t.kind == kind and t.payment_method == method
    assert (t.debit_at is not None) == has_debit
    assert t.amount and t.parsing_source == "rules"

def test_privacy_mask():
    masked = mask_sensitive("010-1234-5678 김지현님 123,456원 카드 1234-1234-1234-1234")
    assert "010-1234-5678" not in masked
    assert "김지현님" not in masked
    assert "123,456원" in masked

def test_swagger_and_dataset():
    assert len(load_dataset().profiles) == 20
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v4/vision/analyze" in paths and "/api/v4/sms/confirm" in paths
    assert client.get("/api/v4/health").status_code == 200

def test_sms_confirm_duplicate_and_isolation():
    payload = {"user_id":"S01","text":"KB국민체크 승인 18,500원 2026/11/05 12:10\n배달의민족"}
    r = client.post("/api/v4/sms/analyze",json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["llm_called"] is False
    t = r.json()["transactions"][0]
    t["uncertainty"] = []
    req = {"user_id":"S01","transaction":t,"acknowledge_unresolved":True}
    saved = client.post("/api/v4/sms/confirm",json=req)
    assert saved.status_code == 200, saved.text
    assert client.post("/api/v4/sms/confirm",json=req).status_code == 409
    assert client.post("/api/v4/sms/analyze",json=payload).json()["transactions"][0]["duplicate_possible"]
    assert client.get("/api/v4/users/S02/confirmed-transactions").json()["count"] == 0
    assert "raw_message" not in str(read_user("S01"))

def test_no_fake_vision():
    png = io.BytesIO()
    Image.new("RGB",(300,600),"white").save(png,format="PNG")
    form = {"user_id":"S01","privacy_acknowledged":"true"}
    result = client.post("/api/v4/vision/analyze",data=form,
        files={"file":("test.png",png.getvalue(),"image/png")})
    assert result.status_code == 503, result.text
    assert result.json()["detail"]["code"] == "vision_api_key_missing"
    assert client.post("/api/v4/vision/analyze",data=form,
        files={"file":("bad.png",b"<svg/>","image/png")}).status_code == 422

def test_candidate_remains_simulated():
    fields = VisionFields(product_name="책",selling_price=12000,quantity=2,
        option_extra_cost=0,shipping_fee=3000,conditional_discount_price=10000)
    assert checkout_total(fields) == 27000
    assert checkout_total(fields,conditional=True) == 23000
    assert checkout_total(fields.model_copy(update={"shipping_fee":None})) is None
    analysis = VisionAnalysis(analysis_id="a"*32,user_id="S01",
        analysis_status="succeeded",model="mocked_test_only",latency_ms=0,product=fields)
    mutate_user("S01",lambda d:d["analyses"].append({
        "image_digest":"test","model":"test","response":analysis.model_dump(mode="json")}))
    response = client.post("/api/v4/purchase-candidates/confirm",json={
        "user_id":"S01","analysis_id":"a"*32,"product":fields.model_dump(),
        "shipping_confirmed":True,"confirm_total":True})
    assert response.status_code == 200,response.text
    candidate = response.json()
    assert candidate["actual_balance_impact"] == 0
    baseline = client.get("/api/v4/users/S01/cashflow").json()
    when = datetime.fromisoformat(load_dataset().meta["snapshot_date"]+"T23:59:59+09:00")+timedelta(days=1)
    simulation = client.post("/api/v4/users/S01/purchase-candidates/"+candidate["id"]+"/simulate",
        json={"debit_at":when.isoformat(),"horizon_days":14})
    assert simulation.status_code == 200,simulation.text
    assert any(e["transaction_status"]=="simulated"
        for d in simulation.json()["daily_cashflow"] for e in d["events"])
    assert client.get("/api/v4/users/S01/cashflow").json()["future_balances"] == baseline["future_balances"]
