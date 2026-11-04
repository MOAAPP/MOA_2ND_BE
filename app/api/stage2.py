"""Stage 2 Swagger endpoints. Never persist original SMS, screenshots or AI credentials."""
from datetime import date, datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Query
from app.api.finance import dataset_dep
from app.schemas import CalculationRequest, ExternalEvent, FinancialResult, UserState
from app.stage2_models import (
    SmsAnalyzeRequest, SmsAnalysis, ConfirmTransaction, ParsedTransaction,
    VisionAnalysis, ConfirmPurchase, PurchaseCandidate, PurchaseSimulationRequest,
)
from app.services.dataset import Dataset
from app.services.finance import calculate, state_for, FinanceProblem
from app.services.stage2_sms import analyze_sms
from app.services.stage2_store import mutate_user, read_user, now_utc
from app.services.stage2_vision import VisionProblem, analyze_image, checkout_total

router = APIRouter(prefix="/api/v4", tags=["v4-stage2-sms"])
purchase_router = APIRouter(prefix="/api/v4", tags=["v4-stage2-vision"])
KST = ZoneInfo("Asia/Seoul")


def fail(code: str, message: str, status: int = 422):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def ensure_user(ds: Dataset, user_id: str):
    try:
        return ds.get_user(user_id)
    except KeyError:
        fail("user_not_found", "공통 금융 데이터셋에 해당 사용자가 없습니다.", 404)


def _ms(stamp: datetime | None) -> int | None:
    return int(stamp.timestamp() * 1000) if stamp else None


def finance_row(item: ParsedTransaction, account_id: str) -> dict:
    kinds = {
        "debit_approval": "spend",
        "credit_approval": "spend",
        "installment_approval": "spend",
        "card_bill": "card_payment",
        "account_in": "income",
        "account_out": "spend",
        "automatic_transfer": "spend",
        "salary": "income",
        "allowance": "income",
        "refund": "refund",
        "cancellation": "cancellation",
        "atm_withdrawal": "atm_withdrawal",
    }
    method = item.payment_method
    if item.kind in ("credit_approval", "installment_approval"):
        method = "credit"
    elif item.kind == "card_bill":
        method = "transfer"
    kind = kinds.get(item.kind, "other")
    # Credit card refunds and cancellations are not independent bank deposits.
    if item.kind in ("cancellation",) or (item.kind == "refund" and method == "credit"):
        kind = "non_cash_adjustment"
    return {
        "id": "sms:" + item.source_id[:32],
        "user_id": item.user_id,
        "account_id": account_id,
        "card_id": item.card_id or item.issuer,
        "occurred_at": _ms(item.occurred_at),
        "debit_at": _ms(item.debit_at),
        "amount": item.amount,
        "name": item.merchant or item.issuer or item.kind,
        "kind": kind,
        "method": method,
        "installments": item.installments,
        "status": "confirmed_user",
        "essential": item.kind in ("card_bill", "automatic_transfer"),
    }


def effective_rows(ds: Dataset, user_id: str) -> list[dict]:
    cutoff = date.fromisoformat(ds.meta["snapshot_date"])
    return [rec["finance_row"] for rec in read_user(user_id)["transactions"]
            if rec.get("finance_row") and rec.get("effective_date") and
            date.fromisoformat(rec["effective_date"]) > cutoff]


@router.post("/sms/analyze", response_model=SmsAnalysis, summary="금융 결제 문자 규칙 파싱 및 중복 탐지",
             description="모든 금융 결제 문자는 규칙 기반 파서만 사용합니다. 외부 AI에 문자를 보내지 않으며 원문은 저장하지 않습니다. 비정형 문자도 수동 확인만 허용합니다.")
async def sms_analyze(request: SmsAnalyzeRequest, ds: Dataset = Depends(dataset_dep)):
    ensure_user(ds, request.user_id)
    try:
        result = await analyze_sms(request.user_id, request.text, datetime.now(KST).date())
    except RuntimeError as exc:
        fail("sms_llm_unavailable", str(exc), 503)
    except Exception as exc:
        # Do not return raw upstream response or sensitive message data.
        if isinstance(exc, HTTPException):
            raise
        fail("sms_llm_failed", "비정형 문자 AI 파싱에 실패했습니다. 직접 정보를 확인해주세요.", 502)
    return result


@router.post("/sms/confirm", summary="사용자 승인 후 별도 파일에 거래 등록",
             description="중복 source_id/fingerprint 거부. 과거 합성 잔액 스냅샷 이전 기록은 장부에만 보관하고 이중 반영하지 않습니다.")
def sms_confirm(request: ConfirmTransaction, ds: Dataset = Depends(dataset_dep)):
    user = ensure_user(ds, request.user_id)
    t = request.transaction
    if t.amount is None or t.amount <= 0 or not t.occurred_at or t.occurred_at.tzinfo is None:
        fail("required_fields_missing", "금액과 거래 날짜·시각을 확인해야 합니다.")
    if t.payment_method == "unknown" or t.kind == "unclassified":
        fail("classification_unresolved", "거래 유형과 결제 수단을 먼저 확정해야 합니다.")
    if t.debit_at and t.debit_at.tzinfo is None:
        fail("debit_at_timezone_missing", "실제 출금 시각에는 시간대가 필요합니다.")
    critical = {"amount", "occurred_at", "payment_method", "transaction_kind"}
    if critical.intersection(t.uncertainty) and not request.acknowledge_unresolved:
        fail("confirmation_required", "불확실한 필드를 확인한 후 acknowledge_unresolved=true로 등록해주세요.")
    account = request.account_id or user.profile["payment_account_id"]
    if account not in {a["account_id"] for a in user.accounts}:
        fail("invalid_account", "사용자 소유의 계좌만 선택할 수 있습니다.")
    cutoff = date.fromisoformat(ds.meta["snapshot_date"])
    effective = t.occurred_at.astimezone(KST).date()
    row = finance_row(t, account)
    # No cash impact when an authorization has not yet received a settlement date.
    if t.transaction_status == "authorization" and t.debit_at is None:
        row["debit_at"] = None
    is_effective = effective > cutoff
    def add(data):
        if any(e["source_id"] == t.source_id or e["fingerprint"] == t.fingerprint
               for e in data["transactions"]):
            return False
        data["transactions"].append({
            "source_id": t.source_id, "fingerprint": t.fingerprint,
            "transaction": t.model_dump(mode="json"), "finance_row": row,
            "account_id": account, "effective_date": effective.isoformat(),
            "confirmed_at": now_utc(), "affects_post_snapshot_balance": is_effective,
        })
        return True
    if not mutate_user(request.user_id, add):
        fail("duplicate_transaction", "동일한 거래가 이미 등록되어 있습니다.", 409)
    warnings = []
    if not is_effective:
        warnings.append("과거 합성 잔액 스냅샷과 중복되지 않도록 실제 잔액에는 재차 반영하지 않았습니다.")
    if t.transaction_status == "authorization" and not t.debit_at:
        warnings.append("신용카드 승인만 기록했습니다. 실제 청구 출금일을 확인해야 현금흐름에 포함됩니다.")
    if row["kind"] == "non_cash_adjustment":
        warnings.append("취소·신용카드 환불은 현금 입금으로 처리하지 않았습니다. 청구 조정은 별도 대조가 필요합니다.")
    return {"status": "confirmed", "transaction_id": row["id"], "user_id": request.user_id,
            "affects_post_snapshot_balance": is_effective and row["kind"] != "non_cash_adjustment",
            "warnings": warnings, "source": "user_confirmed_sms"}


@router.get("/users/{user_id}/confirmed-transactions", summary="확인된 사용자 거래 목록 (원문 미포함)")
def confirmed_transactions(user_id: str, ds: Dataset = Depends(dataset_dep)):
    ensure_user(ds, user_id)
    rows = read_user(user_id)["transactions"]
    return {"user_id": user_id, "transactions": [{
        "transaction": row["transaction"],
        "confirmed_at": row["confirmed_at"],
        "account_id": row["account_id"],
        "affects_post_snapshot_balance": row["affects_post_snapshot_balance"],
    } for row in rows], "count": len(rows)}


@router.get("/users/{user_id}/confirmed-state", response_model=UserState,
            summary="실제 확인 거래가 적용된 현재 계좌 상태")
def confirmed_state(user_id: str, as_of: date | None = Query(default=None),
                    ds: Dataset = Depends(dataset_dep)):
    user = ensure_user(ds, user_id)
    target = as_of or datetime.now(KST).date()
    req = CalculationRequest(user_id=user_id, as_of=target)
    try:
        return state_for(ds, req, observed_overlay=effective_rows(ds, user_id), allow_post_snapshot=True)
    except FinanceProblem as exc:
        fail("invalid_financial_request", str(exc))


@router.get("/users/{user_id}/confirmed-cashflow", response_model=FinancialResult,
            summary="확인된 SMS와 실제 출금일 반영 현금흐름")
def confirmed_cashflow(user_id: str, as_of: date | None = None,
                       horizon_days: int = Query(14, ge=1, le=90),
                       ds: Dataset = Depends(dataset_dep)):
    ensure_user(ds, user_id)
    req = CalculationRequest(user_id=user_id, as_of=as_of or datetime.now(KST).date(),
                             horizon_days=horizon_days)
    try:
        return calculate(ds, req, observed_overlay=effective_rows(ds, user_id), allow_post_snapshot=True)
    except FinanceProblem as exc:
        fail("invalid_financial_request", str(exc))


@purchase_router.post("/vision/analyze", response_model=VisionAnalysis, summary="실제 비전 LLM 쇼핑몰 이미지 분석",
                      description="multipart/form-data 이미지. 원본 이미지 저장 안 함, EXIF 제거 후 비전 LLM 호출. API 키가 없으면 503, 가짜 결과를 반환하지 않습니다.")
async def vision_analyze(user_id: str = Form(..., pattern=r"^S\d\d$"),
                         privacy_acknowledged: bool = Form(...),
                         file: UploadFile = File(...), ds: Dataset = Depends(dataset_dep)):
    ensure_user(ds, user_id)
    if not privacy_acknowledged:
        fail("privacy_confirmation_required", "이미지에 개인정보가 없는지 확인한 뒤 업로드해주세요.")
    if not file.filename or not file.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        fail("unsupported_extension", "PNG, JPG, JPEG, WebP 파일만 허용됩니다.", 415)
    raw = await file.read(8 * 1024 * 1024 + 1)
    try:
        return await analyze_image(user_id, raw, file.content_type or "")
    except VisionProblem as exc:
        fail(exc.code, exc.message, exc.status_code)


@purchase_router.post("/purchase-candidates/confirm", response_model=PurchaseCandidate,
                      summary="수정 가능한 구매 후보 확인·저장 (잔액 차감 없음)")
def confirm_purchase(request: ConfirmPurchase, ds: Dataset = Depends(dataset_dep)):
    ensure_user(ds, request.user_id)
    entries = read_user(request.user_id)["analyses"]
    if not any(a.get("response", {}).get("analysis_id") == request.analysis_id for a in entries):
        fail("analysis_not_found", "선행 비전 분석 ID를 찾지 못했습니다.", 404)
    product = request.product.model_copy(deep=True)
    if not product.product_name or product.selling_price is None or product.quantity is None:
        fail("missing_purchase_fields", "상품명·판매가·수량을 직접 확인해주세요.")
    if request.use_conditional_discount and (
        product.conditional_discount_price is None or not request.discount_eligible_confirmed
    ):
        fail("discount_not_confirmed", "쿠폰·회원가 조건 확인이 필요합니다.")
    total = checkout_total(product, conditional=request.use_conditional_discount)
    if total is None:
        fail("total_unresolved", "옵션 추가금과 배송비를 0원 포함해 직접 확인해주세요.")
    confirmed = bool(request.confirm_total and request.shipping_confirmed)
    if not confirmed:
        fail("checkout_unconfirmed", "배송 조건과 총액을 확인해야 후보를 저장할 수 있습니다.")
    product.needs_user_confirmation = False
    candidate = PurchaseCandidate(id="candidate:" + sha256((request.user_id + ":" + request.analysis_id + ":" + str(total)).encode()).hexdigest()[:24],
        user_id=request.user_id, analysis_id=request.analysis_id, product=product,
        estimated_checkout_total=total, checkout_total_confirmed=True,
        created_at=datetime.now(KST))
    def save(data):
        for e in data["purchase_candidates"]:
            if e["id"] == candidate.id:
                return PurchaseCandidate.model_validate(e)
        data["purchase_candidates"].append(candidate.model_dump(mode="json"))
        return candidate
    return mutate_user(request.user_id, save)


@purchase_router.get("/users/{user_id}/purchase-candidates", response_model=list[PurchaseCandidate],
                     summary="실제 거래와 분리된 구매 후보 목록")
def list_purchase_candidates(user_id: str, ds: Dataset = Depends(dataset_dep)):
    ensure_user(ds, user_id)
    return read_user(user_id)["purchase_candidates"]


@purchase_router.post("/users/{user_id}/purchase-candidates/{candidate_id}/simulate",
                      response_model=FinancialResult, summary="구매 후보를 1회성 가상 이벤트로 시뮬레이션")
def simulate_candidate(user_id: str, candidate_id: str, request: PurchaseSimulationRequest,
                       ds: Dataset = Depends(dataset_dep)):
    u = ensure_user(ds, user_id)
    item = next((x for x in read_user(user_id)["purchase_candidates"] if x["id"] == candidate_id), None)
    if item is None:
        fail("candidate_not_found", "구매 후보를 찾지 못했습니다.", 404)
    if request.debit_at.tzinfo is None:
        fail("debit_at_timezone_missing", "시뮬레이션 출금일은 시간대를 포함해야 합니다.")
    account = request.account_id or u.profile["payment_account_id"]
    amount = item["estimated_checkout_total"]
    event = ExternalEvent(id="simulation:" + candidate_id, user_id=user_id, account_id=account,
                          debit_at=request.debit_at, amount=-amount,
                          kind="proposed_purchase", label=item["product"]["product_name"],
                          payment_method=request.payment_method, status="simulated", source="simulation")
    req = CalculationRequest(user_id=user_id, account_id=account,
                             as_of=request.as_of, horizon_days=request.horizon_days,
                             simulation_events=[event])
    try:
        return calculate(ds, req)
    except FinanceProblem as exc:
        fail("invalid_financial_request", str(exc))
