"""Leakage-safe future variable PAYMENT-ACCOUNT outflows, not credit authorizations.

The synthetic dataset contains oracle future rows and incomplete observations.
Never use either as an ML label or a zero-spend day. No database is required.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from app.core.settings import BACKEND_DIR
from app.schemas import ExternalEvent
from app.services.finance import KST, kst

MODEL_DIR = Path(os.getenv("MOA_ML_MODEL_DIR", str(BACKEND_DIR / "app/data/models"))).resolve()
FEATURES = (
    "weekday", "weekend", "day_of_month", "month_end", "days_from_origin",
    "recent3_mean", "recent7_mean", "recent14_mean", "week7_coverage",
    "week14_coverage", "weekday_mean", "trend", "user_mean",
    "recent_large", "observed_days", "credit_share", "food_share",
    "cafe_share", "transport_share", "shopping_share", "days_to_income",
)
VERSION = "lgbm-cashoutflow-v4.1"
MIN_HISTORY = 7
SEED = 20261105


class ForecastProblem(ValueError):
    pass


def _day(value):
    return date.fromisoformat(str(value)[:10])


def _cash_days(u, cutoff: date):
    """A missing record is None, not 0. Debit-at day is the cash-outflow day."""
    totals = defaultdict(int)
    categories = defaultdict(lambda: defaultdict(int))
    credit = defaultdict(int)
    account = u.profile["payment_account_id"]
    for tx in u.transactions:
        if tx.get("future_scenario") or tx.get("synthetic_oracle_only") or tx.get("phase") == "future_scenario":
            continue
        if tx.get("status") not in ("confirmed_synthetic", "confirmed_user"):
            continue
        occurred = tx.get("occurred_at")
        if not isinstance(occurred, (int, float)) or kst(occurred).date() > cutoff:
            continue
        if tx.get("kind") != "spend" or tx.get("essential") or not isinstance(tx.get("amount"), int):
            continue
        amount = max(0, tx["amount"])
        if tx.get("method") == "credit":
            credit[kst(occurred).date()] += amount
            continue
        if tx.get("account_id") != account or tx.get("method") == "cash":
            continue
        debit = tx.get("debit_at", occurred)
        if not isinstance(debit, (int, float)):
            continue
        when = kst(debit).date()
        if when <= cutoff:
            totals[when] += amount
            categories[when][str(tx.get("category", ""))] += amount
    return totals, credit, categories


def observed_ledger(u, cutoff: date):
    """Only observation_complete days can contribute supervised labels."""
    totals, credit, categories = _cash_days(u, cutoff)
    ledger = {}
    for row in u.daily:
        d = _day(row["date"])
        if d > cutoff or row.get("future_scenario"):
            continue
        complete = row.get("observation_complete") is True
        ledger[d] = {
            "outflow": int(totals.get(d, 0)) if complete else None,
            "complete": complete,
            "known_zero": complete and int(totals.get(d, 0)) == 0,
            "credit": credit.get(d, 0),
            "categories": dict(categories.get(d, {})),
        }
    return ledger


def _past(ledger: dict, origin: date, lookback: int):
    return [ledger.get(origin-timedelta(days=i), {}).get("outflow") for i in range(lookback)]


def _mean(values):
    actual = [v for v in values if v is not None]
    return sum(actual) / len(actual) if actual else 0.0


def _next_income(u, origin: date):
    days = []
    for row in u.schedules:
        if row.get("direction") != "in" or row.get("status") == "uncertain":
            continue
        if row.get("recurrence") == "once" and row.get("date"):
            d = _day(row["date"])
            if d >= origin:
                days.append((d-origin).days)
        if row.get("recurrence") == "monthly" and row.get("day"):
            from calendar import monthrange
            for step in range(3):
                n = origin.year*12+origin.month-1+step
                year, month0 = divmod(n, 12)
                d = date(year,month0+1,min(int(row["day"]),monthrange(year,month0+1)[1]))
                if d >= origin:
                    days.append((d-origin).days)
    return min(days) if days else 35


def feature_row(u, ledger: dict, origin: date, target: date):
    """All monetary features are bounded by origin, never target."""
    if target <= origin:
        raise ForecastProblem("target_must_follow_origin")
    history = [v["outflow"] for d,v in sorted(ledger.items()) if d <= origin and v["outflow"] is not None]
    if len(history) < MIN_HISTORY:
        raise ForecastProblem("insufficient_complete_observation_days")
    r3, r7, r14 = _past(ledger,origin,3),_past(ledger,origin,7),_past(ledger,origin,14)
    prev7 = [ledger.get(origin-timedelta(days=i),{}).get("outflow") for i in range(7,14)]
    weekday = [item["outflow"] for d,item in ledger.items()
               if d <= origin and d.weekday() == target.weekday() and item["outflow"] is not None]
    total_credit = sum(item["credit"] for d,item in ledger.items() if d <= origin)
    total_debit = sum(history)
    cats=defaultdict(int)
    for d,item in ledger.items():
        if d > origin or item["outflow"] is None: continue
        for name,amount in item["categories"].items():
            cats[name]+=amount
    from calendar import monthrange
    month_end=target.day >= monthrange(target.year,target.month)[1]-2
    ratio=lambda key: sum(value for name,value in cats.items() if key in name)/max(1,total_debit)
    return [
        target.weekday(),int(target.weekday()>=5),target.day,int(month_end),(target-origin).days,
        _mean(r3),_mean(r7),_mean(r14),sum(v is not None for v in r7)/7,
        sum(v is not None for v in r14)/14,_mean(weekday) if weekday else _mean(history),
        _mean(r7)-_mean(prev7),_mean(history),max([v for v in r14 if v is not None],default=0),
        len(history),total_credit/max(1,total_credit+total_debit),
        ratio("식"),ratio("카페"),ratio("교통"),ratio("쇼핑"),_next_income(u,origin),
    ]


def baseline_daily(ledger, origin, target, method):
    past = [v["outflow"] for d,v in sorted(ledger.items()) if d <= origin and v["outflow"] is not None]
    if not past: raise ForecastProblem("no_complete_observation")
    if method == "recent7":
        return _mean(_past(ledger,origin,7)) if any(x is not None for x in _past(ledger,origin,7)) else _mean(past[-7:])
    if method == "weekday":
        by_dow = [v["outflow"] for d,v in ledger.items() if d <= origin and
                  d.weekday() == target.weekday() and v["outflow"] is not None]
        return _mean(by_dow) if by_dow else _mean(past)
    raise ForecastProblem("unknown_baseline_method")


def _quality(ledger, as_of):
    last14 = _past(ledger,as_of,14)
    observed = sum(v is not None for v in last14)
    total = sum(x["outflow"] is not None for d,x in ledger.items() if d <= as_of)
    if total < MIN_HISTORY: return "insufficient"
    if observed < 10: return "limited"
    return "complete"


def load_artifact():
    manifest = MODEL_DIR/"metadata.json"
    model_file = MODEL_DIR/"variable_spend.txt"
    if manifest.is_file() != model_file.is_file():
        raise ForecastProblem("model_artifact_incomplete")
    if not (manifest.is_file() and model_file.is_file()):
        return None,None
    try:
        import lightgbm as lgb
        meta=json.loads(manifest.read_text(encoding="utf-8"))
        if meta.get("version") != VERSION or meta.get("features") != list(FEATURES):
            raise ForecastProblem("model_feature_schema_or_version_mismatch")
        return meta,lgb.Booster(model_file=str(model_file))
    except (ValueError, OSError, ImportError) as exc:
        raise ForecastProblem(f"model_artifact_invalid:{exc}") from exc


def run_forecast(ds, user_id: str, as_of: date, horizon=14, artifact=None):
    if horizon < 1 or horizon > 90:
        raise ForecastProblem("invalid_horizon")
    u = ds.get_user(user_id)
    snapshot = _day(ds.meta["snapshot_date"])
    if as_of > snapshot or as_of < _day(ds.meta["start_date"]):
        raise ForecastProblem("forecast_as_of_outside_observation_window")
    ledger=observed_ledger(u,as_of)
    quality=_quality(ledger,as_of)
    meta,model=artifact if artifact is not None else load_artifact()
    if meta and meta.get("dataset_id") != ds.meta["dataset_id"]:
        raise ForecastProblem("artifact_dataset_id_mismatch")
    selected=(meta or {}).get("selected_model","weekday")
    if selected not in ("lightgbm","recent7","weekday"):
        raise ForecastProblem("model_selection_unknown")
    if quality=="insufficient":
        selected="unavailable"
    if selected=="lightgbm" and not model:
        raise ForecastProblem("selected_model_artifact_missing")
    if meta and selected!="unavailable" and _day(meta["training_data_cutoff"]) >= as_of:
        # An evaluation-time test can legitimately use an earlier, fixed model.
        # Never use a model whose training labels extend beyond the prediction origin.
        selected="weekday"
        quality="limited"
    daily=[]
    if selected!="unavailable":
        for i in range(1,horizon+1):
            d=as_of+timedelta(days=i)
            if selected=="lightgbm" and i<=14:
                amount=float(model.predict([feature_row(u,ledger,as_of,d)])[0])
            else:
                method=selected if selected!="lightgbm" else "weekday"
                amount=baseline_daily(ledger,as_of,d,method)
            if not math.isfinite(amount):
                raise ForecastProblem("model_prediction_non_finite")
            daily.append({"date":d.isoformat(),"amount":max(0,round(amount)),
                          "method":selected if selected!="lightgbm" or i<=14 else "weekday_tail"})
    version=(meta or {}).get("version") if selected=="lightgbm" else f"baseline-{selected}-v1"
    run_id=hashlib.sha256(f"{ds.meta['dataset_id']}:{user_id}:{as_of}:{version}:{selected}:{horizon}".encode()).hexdigest()[:24]
    return {
        "user_id":user_id,"forecast_as_of":as_of.isoformat(),"forecast_run_id":run_id,
        "model_type":selected,"model_version":version,
        "training_data_cutoff":(meta or {}).get("training_data_cutoff") if selected=="lightgbm" else as_of.isoformat(),
        "data_quality_status":quality,"observation_start":ds.meta["start_date"],
        "observation_end":ds.meta["snapshot_date"],
        "observed_days":sum(x["outflow"] is not None for x in ledger.values()),
        "horizon_days":horizon,"daily":daily,
        "forecasts":[{"horizon_days":n,"predicted_variable_outflow":sum(x["amount"] for x in daily[:n])}
                     for n in (3,7,14) if n<=horizon and selected!="unavailable"],
        "upper_scenario":{"kind":"assumption_not_calibrated","increase_percent":25,
                          "daily":[{"date":x["date"],"amount":round(x["amount"]*1.25),"method":x["method"]} for x in daily]},
        "is_synthetic":True,"candidate_purchases_included":False,
    }


def to_events(u, forecast, *, upper=False):
    """One forecast event per debit date, NOT a repeated 7-day total."""
    lines=forecast["upper_scenario"]["daily"] if upper else forecast["daily"]
    account=u.profile["payment_account_id"]
    return [ExternalEvent(
        id=f"ml:{forecast['forecast_run_id']}:{item['date']}",
        user_id=u.user_id,account_id=account,
        debit_at=datetime.combine(_day(item["date"]),time(12),KST),
        amount=-item["amount"],kind="future_variable_cash_outflow",
        label="향후 변동 지출 전망",payment_method="debit",
        status="predicted",
        source="model" if forecast["model_type"]=="lightgbm" and item.get("method","lightgbm")=="lightgbm" else "rules",
        forecast_run_id=forecast["forecast_run_id"],
    ) for item in lines]
