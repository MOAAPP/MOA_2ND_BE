"""Deterministic financial-SMS parsing. Rule-supported messages consume zero LLM tokens."""
import hashlib
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from app.stage2_models import ParsedTransaction, SmsAnalysis
from app.services.stage2_store import read_user

KST = ZoneInfo("Asia/Seoul")
ISSUER = re.compile(r"(KB국민|국민|신한|삼성|현대|롯데|하나|우리|NH농협|농협|카카오뱅크|카카오페이|토스뱅크|토스|케이뱅크|IBK기업|기업|BC|비씨|우체국|새마을금고)", re.I)
AMOUNT = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{3,10})\s*원(?!\w)")
DATE = re.compile(r"(?<!\d)(?:(20\d{2})[./-])?(\d{1,2})[./-](\d{1,2})(?!\d)")
TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
INSTALLMENTS = re.compile(r"할부\s*(\d{1,2})\s*개?월?")
BALANCE_LINE = re.compile(r"(잔액|누적|한도|가용|포인트|적립|쿠폰|혜택)")
SENSITIVE = [
    (re.compile(r"(?<!\d)01[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)\d{2,6}[- ]\d{2,6}[- ]\d{2,6}(?!\d)"), "[계좌번호]"),
    (re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)"), "[카드번호]"),
    (re.compile(r"\b[가-힣]{2,4}\s*님\b"), "[성명]"),
    (re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"), "[주민번호]"),
]


def mask_sensitive(text: str) -> str:
    for regex, repl in SENSITIVE:
        text = regex.sub(repl, text)
    return text


def chunks(text: str) -> list[str]:
    # Repeated web발신 headers and empty lines are separate SMS messages.
    text = re.sub(r"(?<!^)(?=\[Web발신\]|\[WEB발신\])", "\n\n", text)
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _kind(raw: str) -> tuple[str, str, str]:
    if re.search(r"카드\s*(?:이용)?대금|카드값\s*출금|카드\s*청구|청구\s*금액\s*출금", raw):
        return "card_bill", "transfer", "posted"
    if re.search(r"승인\s*취소|결제\s*취소|거래\s*취소", raw):
        return "cancellation", "unknown", "cancelled"
    if re.search(r"환불|취소\s*입금", raw):
        return "refund", "unknown", "posted"
    if re.search(r"ATM|현금\s*인출|현금\s*출금", raw, re.I):
        return "atm_withdrawal", "atm", "posted"
    if re.search(r"(월급|급여|급여입금|알바비)", raw):
        return "salary", "transfer", "posted"
    if re.search(r"용돈", raw):
        return "allowance", "transfer", "posted"
    if re.search(r"내\s*계좌|본인\s*계좌", raw):
        return "account_out" if "출금" in raw else "account_in", "transfer", "posted"
    if re.search(r"자동\s*이체|자동\s*납부|자동\s*출금", raw):
        return "automatic_transfer", "transfer", "posted"
    if re.search(r"입금|입금완료", raw):
        return "account_in", "transfer", "posted"
    if re.search(r"출금|이체완료|송금", raw) and not re.search(r"카드\s*승인", raw):
        return "account_out", "transfer", "posted"
    if re.search(r"할부", raw) and re.search(r"승인|결제", raw):
        return "installment_approval", "credit", "authorization"
    if re.search(r"체크", raw) and re.search(r"승인|결제", raw):
        return "debit_approval", "debit", "posted"
    if re.search(r"신용|카드", raw) and re.search(r"승인|결제", raw):
        return "credit_approval", "credit", "authorization"
    return "unclassified", "unknown", "unconfirmed"


def _datetime(raw: str, reference: date) -> datetime | None:
    d = DATE.search(raw)
    if not d:
        return None
    y, m, day = int(d[1] or reference.year), int(d[2]), int(d[3])
    try:
        parsed = date(y, m, day)
    except ValueError:
        return None
    if not d[1] and parsed > reference + timedelta(days=1):
        parsed = date(y - 1, m, day)
    t = TIME.search(raw)
    return datetime.combine(parsed, time(int(t[1]), int(t[2])) if t else time(12), KST)


def _merchant(raw: str, issuer: str | None) -> str | None:
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    for line in reversed(lines):
        if AMOUNT.search(line) or DATE.search(line) or BALANCE_LINE.search(line):
            continue
        if re.search(r"(Web발신|승인|결제|출금|입금|계좌|카드|할부|본인|님|\d{4})", line):
            continue
        if issuer and issuer in line:
            continue
        return line[:100]
    return None


def parse_one(user_id: str, raw: str, reference: date) -> ParsedTransaction | None:
    message = " ".join(raw.split())
    source_id = sha(user_id + "|" + message)
    issuer_match = ISSUER.search(raw)
    issuer = issuer_match.group(1) if issuer_match else None
    kind, method, status = _kind(raw)
    # Do not misinterpret running balance, limits or accumulated card spending as transaction amounts.
    cleaned = "\n".join(l for l in raw.splitlines() if not BALANCE_LINE.search(l))
    amounts = AMOUNT.findall(cleaned)
    amount = int(amounts[0].replace(",", "")) if amounts else None
    if kind == "unclassified" and amount is None:
        return None
    occurred = _datetime(raw, reference)
    installment_match = INSTALLMENTS.search(raw)
    merchant = _merchant(raw, issuer)
    uncertain = []
    if amount is None:
        uncertain.append("amount")
    if occurred is None:
        uncertain.append("occurred_at")
    elif not TIME.search(raw):
        uncertain.append("occurred_at_time")
    if method == "unknown":
        uncertain.append("payment_method")
    if merchant is None and kind in ("debit_approval", "credit_approval", "installment_approval"):
        uncertain.append("merchant")
    # A credit authorization is a liability, NOT a bank-account debit.
    debit_at = occurred if status == "posted" and method not in ("credit", "unknown") else None
    if kind == "card_bill":
        debit_at = occurred
    if kind in ("credit_approval", "installment_approval"):
        uncertain.append("debit_at")
    if kind in ("refund", "cancellation") and method == "unknown":
        uncertain.append("refund_settlement_method")
    if kind == "unclassified":
        uncertain.append("transaction_kind")
    fingerprint = sha("|".join([user_id, kind, issuer or "", method, merchant or "",
                              str(amount), occurred.isoformat() if occurred else ""]))
    confidence = max(0.1, 0.98 - len(uncertain) * 0.12 - (0.3 if kind == "unclassified" else 0))
    return ParsedTransaction(
        source_id=source_id, fingerprint=fingerprint, user_id=user_id, kind=kind,
        issuer=issuer, payment_method=method, merchant=merchant, amount=amount,
        occurred_at=occurred, debit_at=debit_at, installments=int(installment_match[1]) if installment_match else None,
        transaction_status=status, uncertainty=uncertain, confidence=round(confidence, 2)
    )


async def analyze_sms(user_id: str, text: str, reference: date) -> SmsAnalysis:
    """Rule-based only: no external model, network call or AI fallback."""
    saved = read_user(user_id)["transactions"]
    seen = {t["source_id"] for t in saved} | {t["fingerprint"] for t in saved}
    parsed = []
    skipped = 0
    for block in chunks(text):
        one = parse_one(user_id, block, reference)
        if one is None:
            skipped += 1
            continue
        one.duplicate_possible = one.source_id in seen or one.fingerprint in seen
        parsed.append(one)
        seen.add(one.source_id)
        seen.add(one.fingerprint)
    return SmsAnalysis(
        analysis_status="needs_manual_review" if any(x.uncertainty for x in parsed) or skipped else "parsed",
        transactions=parsed, skipped=skipped, llm_called=False,
        masked_before_llm=False,
        warnings=["결제 문자는 규칙 기반으로만 분석합니다. 외부 AI로 전송하지 않습니다.",
                  "원문은 저장하지 않습니다. 금액·날짜·실제 출금일을 직접 확인하세요."]
    )
