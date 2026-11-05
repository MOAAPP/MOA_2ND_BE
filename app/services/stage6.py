"""Stage 6: deterministic, observable personal cash-flow briefing for the synthetic demo.

No LLM is needed to select events. Do not convert synthetic future/oracle rows
to confirmed observations. All money calculations delegate to the Stage 1/4 engine.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.core.settings import BACKEND_DIR
from app.ml.forecast import ForecastProblem, run_forecast, to_events, observed_ledger
from app.schemas import CalculationRequest, ExternalEvent
from app.services.dataset import Dataset
from app.services.finance import FinanceProblem, KST, calculate

RUNTIME = Path(os.getenv("MOA_STAGE6_STORAGE_DIR", str(BACKEND_DIR / "app/data/runtime/stage6"))).resolve()
USER_RE = re.compile(r"^S[0-9]{2}$")
MAX_CREDIT_CAP = 100_000_000


def _day(ds: Dataset, value: date | None) -> date:
    day = value or date.fromisoformat(ds.meta["snapshot_date"])
    if day < date.fromisoformat(ds.meta["start_date"]) or day > date.fromisoformat(ds.meta["snapshot_date"]):
        raise FinanceProblem("observation_date_out_of_range")
    return day


def _user(ds: Dataset, user_id: str):
    if not USER_RE.fullmatch(user_id):
        raise KeyError(user_id)
    return ds.get_user(user_id)


def _forecast(ds: Dataset, user_id: str, day: date, horizon: int):
    try:
        result = run_forecast(ds, user_id, day, horizon)
        events = to_events(_user(ds, user_id), result) if result["model_type"] != "unavailable" else []
        return result, events
    except ForecastProblem as exc:
        return {"model_type": "unavailable", "model_version": None, "daily": [],
                "data_quality_status": "unavailable", "error": str(exc),
                "forecasts": []}, []


def evaluate(ds: Dataset, user_id: str, as_of: date | None, *, horizon: int = 14,
             minimum_balance: int = 0, income_delay_days: int = 0):
    if not 1 <= horizon <= 90 or minimum_balance < 0 or not 0 <= income_delay_days <= 31:
        raise FinanceProblem("invalid_horizon_or_constraint")
    u = _user(ds, user_id)
    day = _day(ds, as_of)
    forecast, events = _forecast(ds, user_id, day, horizon)
    req = CalculationRequest(user_id=user_id, as_of=day, horizon_days=horizon,
                             minimum_required_balance=minimum_balance,
                             income_delay_days=income_delay_days,
                             forecast_events=events)
    result = calculate(ds, req)
    return u, day, req, result, forecast


def spend_capacity(ds: Dataset, user_id: str, as_of: date | None, *, horizon: int = 14,
                   minimum_balance: int = 0, income_delay_days: int = 0,
                   payment_method: str = "debit", credit_debit_date: date | None = None):
    u, day, req, baseline, forecast = evaluate(
        ds, user_id, as_of, horizon=horizon, minimum_balance=minimum_balance,
        income_delay_days=income_delay_days)
    if payment_method not in ("debit", "credit"):
        raise FinanceProblem("payment_method_must_be_debit_or_credit")
    baseline_ok = baseline.minimum_balance >= minimum_balance and baseline.essential_payments_covered is True
    # Immediate debit is a constant downward shift in every future balance and the
    # current cash balance. Hence the pointwise minimum is algebraically equivalent
    # to repeated re-simulation for a zero-fee, no-interest debit purchase.
    if payment_method == "debit":
        cap = max(0, min(baseline.available_cash, baseline.minimum_balance - minimum_balance)) if baseline_ok else 0
        method = "cashflow_prefix_minimum_v1"
        bounded = False
    else:
        if credit_debit_date is None or not (day < credit_debit_date <= day + timedelta(days=horizon)):
            raise FinanceProblem("credit_debit_date_must_be_within_projection_window_and_after_as_of")
        # Credit purchases do not reduce today's account balance, only the
        # confirmed future settlement date. Always reuse the Python engine.
        settle = datetime.combine(credit_debit_date, time(12), KST)
        def affordable(amount: int) -> bool:
            event = ExternalEvent(id="capacity:credit:settlement", user_id=user_id,
                account_id=baseline.account_id, debit_at=settle, amount=-amount,
                kind="credit_card_bill", label="가상 신용카드 출금", payment_method="credit",
                status="simulated", source="simulation")
            projection = calculate(ds, req.model_copy(update={"simulation_events": [event]}))
            return projection.minimum_balance >= minimum_balance and projection.essential_payments_covered is True
        low, high = 0, MAX_CREDIT_CAP
        if baseline_ok:
            while low < high:
                mid = (low + high + 1) // 2
                if affordable(mid):
                    low = mid
                else:
                    high = mid - 1
        cap = low if baseline_ok else 0
        method = "python_engine_binary_search_v1"
        bounded = cap == MAX_CREDIT_CAP
    return {
        "user_id": user_id, "as_of_date": day.isoformat(),
        "data_source": ds.meta["dataset_id"], "is_synthetic": True,
        "payment_method": payment_method,
        "credit_debit_date": credit_debit_date.isoformat() if credit_debit_date else None,
        "horizon_days": horizon, "minimum_required_balance": minimum_balance,
        "income_delay_days": income_delay_days,
        "current_cash": baseline.available_cash, "projected_minimum_balance": baseline.minimum_balance,
        "baseline_constraints_met": baseline_ok, "estimated_additional_spend": cap,
        "calculation_method": method, "capped_by_search_limit": bounded,
        "forecast_model_type": forecast["model_type"],
        "forecast_model_version": forecast.get("model_version"),
        "predictive_not_permission": True,
        "message": ("이미 미래 결제일 또는 최소 잔액 조건에 위험이 있어 추가 지출 여유를 0원으로 표시해요."
                    if not baseline_ok else
                    "예측과 등록된 일정을 기준으로 계산한 참고 금액이며 소비 허가액이 아니에요."),
    }


def _observed(tx: dict, day: date) -> bool:
    if tx.get("future_scenario") or tx.get("synthetic_oracle_only") or tx.get("phase") == "future_scenario":
        return False
    return tx.get("status") in ("confirmed_synthetic", "confirmed_user") and tx.get("date", "9999") <= day.isoformat()


def _facts(ds: Dataset, user_id: str, day: date, minimum_balance: int):
    u, _, req, result, forecast = evaluate(ds, user_id, day, minimum_balance=minimum_balance)
    observed = sorted(t.get("id") for t in u.transactions if _observed(t, day) and t.get("id"))
    essential = [
        {"date": x["date"], "name": x["name"], "amount": x["amount"], "covered": x["covered"],
         "balance_after": x["balance_after"]}
        for x in result.essential_payment_results
    ]
    virtual = result.virtual_investments
    return {
        "user_id": user_id, "as_of_date": day.isoformat(),
        "dataset_id": ds.meta["dataset_id"], "minimum_balance": result.minimum_balance,
        "available_cash": result.available_cash,
        "forecast_total": sum(x["amount"] for x in forecast["daily"]),
        "forecast_type": forecast["model_type"], "forecast_version": forecast.get("model_version"),
        "observed_transaction_ids": observed,
        "essential_payments": essential,
        "virtual_investments": virtual,
        "savings_goals": result.savings_goals,
        "nearby_essential": essential[:5],
        "forecast": forecast,
    }


def _path(uid: str, day: str) -> Path:
    if not USER_RE.fullmatch(uid):
        raise KeyError(uid)
    return RUNTIME / uid / "analyses" / (day + ".json")


def _atomic(target: Path, value: dict):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _stored_previous(uid: str, today: date):
    directory = RUNTIME / uid / "analyses"
    if not directory.is_dir():
        return None
    eligible = []
    for path in directory.glob("????-??-??.json"):
        try:
            d = date.fromisoformat(path.stem)
            if d <= today:
                eligible.append((d, path))
        except ValueError:
            continue
    if not eligible:
        return None
    for _, path in sorted(eligible, reverse=True):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Corrupt analysis cannot be treated as an empty or valid snapshot.
            raise FinanceProblem("analysis_snapshot_corrupt")
    return None


def detect_changes(before: dict | None, now: dict):
    if before is None:
        return {"status": "first_analysis", "events": [], "previous_as_of_date": None,
                "message": "첫 분석이어서 비교할 이전 분석 기록이 없어요."}
    if before["user_id"] != now["user_id"]:
        raise FinanceProblem("analysis_user_mismatch")
    events = []
    def add(code, significance, title, delta=None):
        events.append({"code": code, "importance": significance, "title": title, "delta": delta})
    added = sorted(set(now["observed_transaction_ids"]) - set(before.get("observed_transaction_ids", [])))
    if added:
        add("new_transactions", "medium", f"새로 확인된 거래 {len(added)}건", len(added))
    diff = now["minimum_balance"] - before["minimum_balance"]
    if diff < 0:
        add("minimum_balance_decreased", "high" if now["minimum_balance"] < 0 else "medium",
            "예상 최저 잔액이 낮아졌어요", diff)
    first_due = {(p["date"], p["name"], p["amount"]) for p in before.get("essential_payments", [])}
    due_added = [x for x in now["essential_payments"]
                 if (x["date"], x["name"], x["amount"]) not in first_due and not x["covered"]]
    if due_added:
        add("new_essential_risk", "high", f"확인이 필요한 필수결제 {len(due_added)}건", len(due_added))
    if now["forecast_total"] > before.get("forecast_total", 0):
        add("variable_forecast_increased", "medium", "향후 변동 지출 전망이 증가했어요",
            now["forecast_total"] - before["forecast_total"])
    if now["virtual_investments"] != before.get("virtual_investments"):
        add("virtual_investment_changed", "low", "가상투자 보유 상태가 달라졌어요")
    if now["savings_goals"] != before.get("savings_goals"):
        add("savings_goal_changed", "medium", "저축 목표가 변경되었어요")
    return {"status": "changed" if events else "unchanged", "events": events,
            "previous_as_of_date": before["as_of_date"],
            "message": "관측된 데이터와 계산 결과의 변화를 비교했어요."}


def briefing(ds: Dataset, user_id: str, as_of: date | None, minimum_balance: int = 0):
    day = _day(ds, as_of)
    u = _user(ds, user_id)
    facts = _facts(ds, user_id, day, minimum_balance)
    previous = _stored_previous(user_id, day)
    changes = detect_changes(previous, facts)
    capacity = spend_capacity(ds, user_id, day, minimum_balance=minimum_balance)
    next_due = facts["nearby_essential"][0] if facts["nearby_essential"] else None
    titles = [event["title"] for event in changes["events"][:3]]
    text = " · ".join(titles) if titles else (
        "처음 분석한 금융 상태예요." if changes["status"] == "first_analysis"
        else "이전 분석 이후 새로 감지된 주요 변화가 없어요.")
    plan_status = list_saved_plans(ds, user_id)["plans"]
    if not previous or previous.get("as_of_date") != day.isoformat() or previous != facts:
        # Immutable snapshots by date: retain first analysis for the day. A subsequent
        # same-day request compares against the first snapshot but never rewrites it.
        if not _path(user_id, day.isoformat()).exists():
            _atomic(_path(user_id, day.isoformat()), facts)
    return {
        "user_id": user_id, "as_of_date": day.isoformat(),
        "data_source": ds.meta["dataset_id"], "is_synthetic": True,
        "generation_mode": "rule_template", "llm_used": False,
        "summary": text, "change_detection": changes,
        "spending_capacity": capacity, "nearest_essential_payment": next_due,
        "projected_minimum_balance": facts["minimum_balance"],
        "variable_forecast": facts["forecast"],
        "virtual_investments": facts["virtual_investments"],
        "savings_goals": facts["savings_goals"],
        "saved_plans_count": len(plan_status),
        "saved_plans": plan_status[:3],
        "news": {"status": "provider_not_configured", "articles": [], "is_real_news": False},
        "limitations": ["합성 데이터 기반 전망", "실제 금융 계좌 아님", "실제 뉴스 연결 없음",
                        "원본 데이터에 저축 목표가 없으면 목표 날짜를 만들지 않음"],
    }


def list_saved_plans(ds: Dataset, user_id: str):
    _user(ds, user_id)
    from app.api.agent import SAVE_DIR
    from app.api.stage3 import STORAGE_DIR
    plans = []
    for kind, directory in (("world", STORAGE_DIR), ("agent", SAVE_DIR)):
        if not directory.is_dir():
            continue
        for file in directory.glob("*.json"):
            try:
                record = json.loads(file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if record.get("user_id") != user_id or record.get("dataset_id") != ds.meta["dataset_id"]:
                continue
            plans.append({"id": record.get("id"), "kind": kind, "label": record.get("label"),
                          "saved_at": record.get("saved_at"),
                          "analysis_as_of": record.get("as_of") or record.get("request", {}).get("as_of"),
                          "has_immutable_snapshot": "result_snapshot" in record})
    plans.sort(key=lambda p: p["saved_at"] or "", reverse=True)
    return {"user_id": user_id, "plans": plans, "is_synthetic": True}


def track_plan(ds: Dataset, user_id: str, plan_id: str, as_of: date | None):
    _user(ds, user_id)
    day = _day(ds, as_of)
    if not re.fullmatch(r"[a-f0-9]{32}", plan_id):
        raise KeyError(plan_id)
    from app.api.agent import SAVE_DIR
    from app.api.stage3 import STORAGE_DIR
    for kind, directory in (("world", STORAGE_DIR), ("agent", SAVE_DIR)):
        file = directory / (plan_id + ".json")
        if not file.is_file():
            continue
        try:
            record = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise FinanceProblem("saved_plan_corrupt") from exc
        if record.get("user_id") != user_id:
            raise KeyError(plan_id)
        if record.get("dataset_id") != ds.meta["dataset_id"]:
            raise FinanceProblem("saved_plan_dataset_changed")
        saved_day = date.fromisoformat(record.get("as_of") or record["request"]["as_of"])
        if day < saved_day:
            raise FinanceProblem("tracking_day_before_saved_analysis")
        snapshot = record.get("result_snapshot")
        forecast = record.get("forecast_snapshot")
        if snapshot is None:
            return {"user_id": user_id, "id": plan_id, "kind": kind,
                    "status": "legacy_snapshot_unavailable", "snapshot_preserved": False,
                    "message": "기존 저장 기록에 당시의 전체 계산 결과가 없어 비교를 확정할 수 없어요."}
        ledger = observed_ledger(_user(ds, user_id), day)
        rows = (forecast or {}).get("daily", [])
        observations = []
        for row in rows:
            d = date.fromisoformat(row["date"])
            if saved_day < d <= day:
                actual = ledger.get(d)
                observations.append({
                    "date": row["date"], "predicted": row["amount"],
                    "observed": actual["outflow"] if actual and actual["complete"] else None,
                    "classification": ("prediction_difference" if actual and actual["complete"]
                                       else "observation_unavailable"),
                })
        return {"user_id": user_id, "id": plan_id, "kind": kind,
                "saved_analysis_date": saved_day.isoformat(), "tracking_date": day.isoformat(),
                "status": "compared" if observations else "no_new_observable_days",
                "snapshot_preserved": True,
                "saved_result": snapshot,
                "daily_spend_comparison": observations,
                "note": "관측 누락과 예측 오차를 구분하며 구매 여부는 거래 확인 없이 단정하지 않아요."}
    raise KeyError(plan_id)
