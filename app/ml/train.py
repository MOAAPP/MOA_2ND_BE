"""Offline train/validation/test by time. Never retrain inside API requests.

Compares LightGBM (3/7/14-day horizons) against two mean baselines plus the weekday baseline
with identical complete-observation cases, then writes metrics and an error analysis.
"""
from __future__ import annotations
import argparse
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import numpy as np
from app.ml.forecast import (
    FEATURES, SEED, VERSION, MODEL_DIR, MAX_MODEL_HORIZON, METHODS, observed_ledger,
    feature_row, baseline_daily, normalise_features, predict_lightgbm, _day, ForecastProblem,
)
from app.services.dataset import load_dataset

HORIZONS = (3, 7, 14)
LABELS = {"overall": "전체 평균", "recent7": "최근 7일 이동평균", "weekday": "요일별 평균", "lightgbm": "LightGBM"}
PARAMS = dict(objective="huber", alpha=1.0, n_estimators=80, num_leaves=5, max_depth=3,
              min_child_samples=40, learning_rate=0.04, reg_lambda=20, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.8)


def _examples(ds, train_until):
    """Multi-horizon rows (origin -> origin+h, h=1..14) so days_from_origin is really learned.

    A label is used only if its target date is before train_until (never validation/test dates).
    """
    rows, labels = [], []
    for uid in sorted(ds.profiles):
        u = ds.get_user(uid)
        ledger = observed_ledger(u, _day(ds.meta["snapshot_date"]))
        for origin in sorted(ledger):
            for h in range(1, MAX_MODEL_HORIZON + 1):
                target = origin + timedelta(days=h)
                if target >= train_until or target not in ledger or ledger[target]["outflow"] is None:
                    continue
                try:
                    rows.append(feature_row(u, ledger, origin, target))
                except ForecastProblem:
                    continue
                labels.append(ledger[target]["outflow"])
    return np.asarray(rows, dtype=float), np.asarray(labels, dtype=float)


def _fit(x, y):
    import lightgbm as lgb
    scaled, scale = normalise_features(x)
    return lgb.LGBMRegressor(**PARAMS, random_state=SEED, n_jobs=1, verbosity=-1).fit(scaled, y / scale)


class _Booster:
    """Adapter so training-time and inference-time use the same predict_lightgbm path."""
    def __init__(self, fitted): self.fitted = fitted
    def predict(self, x): return self.fitted.predict(x)


def _stats(errors, daily):
    """errors: signed horizon-sum errors (pred-truth); daily: signed per-day errors."""
    if not errors:
        return {"cases": 0, "mae": None, "rmse": None, "bias": None, "daily_mae": None, "daily_rmse": None}
    e, d = np.asarray(errors, float), np.asarray(daily, float)
    return {"cases": len(e), "mae": round(float(np.abs(e).mean()), 2),
            "rmse": round(float(math.sqrt((e ** 2).mean())), 2), "bias": round(float(e.mean()), 2),
            "daily_mae": round(float(np.abs(d).mean()), 2),
            "daily_rmse": round(float(math.sqrt((d ** 2).mean())), 2)}


def _evaluate(ds, meta, model, first, last):
    """All models see identical complete future target dates within one period."""
    from app.schemas import CalculationRequest
    from app.services.finance import calculate
    from app.ml.forecast import to_events
    errors = defaultdict(list); daily_err = defaultdict(list); per_user = defaultdict(lambda: defaultdict(list))
    balances = defaultdict(list); missed = defaultdict(int); false_warning = defaultdict(int)
    upper = defaultdict(list); origins = defaultdict(set); records = []
    for uid in sorted(ds.profiles):
        u = ds.get_user(uid)
        ledger = observed_ledger(u, _day(ds.meta["snapshot_date"]))
        for origin in sorted(d for d in ledger if first <= d <= last):
            try:
                feature_row(u, ledger, origin, origin + timedelta(days=1))
            except ForecastProblem:
                continue
            for h in HORIZONS:
                target_dates = [origin + timedelta(days=i) for i in range(1, h + 1)]
                if target_dates[-1] > last or any(d not in ledger or ledger[d]["outflow"] is None for d in target_dates):
                    continue
                true_daily = [ledger[d]["outflow"] for d in target_dates]
                truth = sum(true_daily)
                forecasts = {}
                for method in METHODS:
                    if method == "lightgbm":
                        vals = [round(float(v)) for v in predict_lightgbm(model, u, ledger, origin, target_dates)]
                    else:
                        vals = [max(0, round(baseline_daily(ledger, origin, d, method))) for d in target_dates]
                    forecasts[method] = vals
                    signed = sum(vals) - truth
                    errors[(method, h)].append(signed)
                    daily_err[(method, h)].extend(v - t for v, t in zip(vals, true_daily))
                    per_user[(uid, method, h)]["mae"].append(abs(signed))
                    upper[(method, h)].append(int(truth <= sum(round(x * 1.25) for x in vals)))
                    records.append({"user_id": uid, "origin": origin.isoformat(), "horizon": h, "method": method,
                                    "truth": truth, "pred": sum(vals), "error": signed,
                                    "daily": [(d.isoformat(), t, v) for d, t, v in zip(target_dates, true_daily, vals)]})
                origins[(uid, h)].add(origin.isoformat())
                # Financial risk proxy: identical scheduled obligations + realised variable cash only.
                for method, vals in forecasts.items():
                    f = {"user_id": uid, "forecast_run_id": f"eval:{uid}:{origin}:{method}", "model_type": method,
                         "daily": [{"date": d.isoformat(), "amount": v} for d, v in zip(target_dates, vals)]}
                    truth_f = {**f, "daily": [{"date": d.isoformat(), "amount": v} for d, v in zip(target_dates, true_daily)]}
                    truth_out = calculate(ds, CalculationRequest(user_id=uid, as_of=origin, horizon_days=h,
                                                                 forecast_events=to_events(u, truth_f)))
                    predicted = calculate(ds, CalculationRequest(user_id=uid, as_of=origin, horizon_days=h,
                                                                 forecast_events=to_events(u, f)))
                    balances[(method, h)].append(abs(predicted.future_balances[str(h)] - truth_out.future_balances[str(h)]))
                    if truth_out.minimum_balance < 0 <= predicted.minimum_balance:
                        missed[(method, h)] += 1
                    if predicted.minimum_balance < 0 <= truth_out.minimum_balance:
                        false_warning[(method, h)] += 1
    summary = {}
    for method in METHODS:
        summary[method] = {}
        for h in HORIZONS:
            key = (method, h)
            summary[method][str(h)] = {
                **_stats(errors[key], daily_err[key]),
                "balance_mae_proxy": round(sum(balances[key]) / len(balances[key]), 2) if balances[key] else None,
                "missed_shortfalls_proxy": missed[key], "false_warnings_proxy": false_warning[key],
                "upper_25pct_coverage_assumption": round(sum(upper[key]) / len(upper[key]), 4) if upper[key] else None,
            }
    users = {uid: {"cases": {str(h): len(origins[(uid, h)]) for h in HORIZONS},
                   "mae_7": {m: round(float(np.mean(per_user[(uid, m, 7)]["mae"])), 2) if per_user[(uid, m, 7)]["mae"] else None
                             for m in METHODS}}
             for uid in sorted(ds.profiles)}
    return summary, users, records


def error_analysis(records, ds):
    """Tables that explain WHERE each model is wrong (daily level, comparable across models)."""
    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    user_mean = {}
    for uid in ds.profiles:
        ledger = observed_ledger(ds.get_user(uid), _day(ds.meta["snapshot_date"]))
        vals = [v["outflow"] for v in ledger.values() if v["outflow"] is not None]
        user_mean[uid] = (sum(vals) / len(vals)) if vals else 0
    by_weekday = defaultdict(list); by_size = defaultdict(list)
    for r in records:
        if r["horizon"] != 7:  # 7-day cases are the model-selection horizon
            continue
        for day, truth, pred in r["daily"]:
            wd = weekday_names[date.fromisoformat(day).weekday()]
            by_weekday[(r["method"], wd)].append(pred - truth)
            ratio = truth / max(1.0, user_mean[r["user_id"]])
            bucket = "저지출(<0.5×평균)" if ratio < 0.5 else "보통(0.5~1.5×)" if ratio <= 1.5 else "고지출(>1.5×)"
            by_size[(r["method"], bucket)].append(pred - truth)
    def row(keyname, key, errs, method):
        e = np.asarray(errs, float)
        return {keyname: key, "method": method, "days": len(e), "daily_mae": round(float(np.abs(e).mean()), 2),
                "bias": round(float(e.mean()), 2)}
    table_weekday = [row("weekday", wd, by_weekday[(m, wd)], m) for m in METHODS for wd in weekday_names if by_weekday[(m, wd)]]
    buckets = ["저지출(<0.5×평균)", "보통(0.5~1.5×)", "고지출(>1.5×)"]
    table_size = [row("spend_level", b, by_size[(m, b)], m) for m in METHODS for b in buckets if by_size[(m, b)]]
    worst = sorted((r for r in records if r["horizon"] == 7 and r["method"] == "lightgbm"),
                   key=lambda r: abs(r["error"]), reverse=True)[:10]
    worst_cases = [{"user_id": r["user_id"], "origin": r["origin"], "horizon_days": 7, "actual_total": r["truth"],
                    "predicted_total": r["pred"], "error": r["error"],
                    "baseline_weekday_error": next((x["error"] for x in records if x["user_id"] == r["user_id"]
                                                    and x["origin"] == r["origin"] and x["horizon"] == 7 and x["method"] == "weekday"), None)}
                   for r in worst]
    return {"horizon_days": 7, "note": "bias = 예측-실제 (음수=과소예측). 일 단위 오차, 테스트 구간.",
            "by_weekday": table_weekday, "by_spend_level": table_size, "worst_cases_lightgbm": worst_cases}


def _flat_metrics(split):
    return [{"method": m, "label": LABELS[m], "horizon_days": int(h), **v}
            for m in METHODS for h, v in split[m].items()]


def render_markdown(report):
    md = ["# 변동지출 예측 성능 비교 (합성 데이터, 테스트 구간)", ""]
    for split in ("validation", "test"):
        md += [f"## {split} 구간 — 호라이즌 합계 오차 (원)", "",
               "| 모델 | 기간 | 사례 | MAE | RMSE | 편향(예측-실제) | 일 MAE | 일 RMSE |", "|---|---|---|---|---|---|---|---|"]
        for r in report["metrics"][split]:
            md.append(f"| {r['label']} | {r['horizon_days']}일 | {r['cases']} | {r['mae']} | {r['rmse']} | {r['bias']} | {r['daily_mae']} | {r['daily_rmse']} |")
        md.append("")
    ea = report["error_analysis"]
    md += ["## 오류 분석 — 요일별 (7일 호라이즌, 일 단위)", "", "| 요일 | 모델 | 일수 | 일 MAE | 편향 |", "|---|---|---|---|---|"]
    md += [f"| {r['weekday']} | {LABELS[r['method']]} | {r['days']} | {r['daily_mae']} | {r['bias']} |" for r in ea["by_weekday"]]
    md += ["", "## 오류 분석 — 지출 규모별", "", "| 구간 | 모델 | 일수 | 일 MAE | 편향 |", "|---|---|---|---|---|"]
    md += [f"| {r['spend_level']} | {LABELS[r['method']]} | {r['days']} | {r['daily_mae']} | {r['bias']} |" for r in ea["by_spend_level"]]
    md += ["", "## LightGBM 7일 최대 오차 사례", "", "| 사용자 | 기준일 | 실제 | 예측 | 오차 | 요일평균 오차 |", "|---|---|---|---|---|---|"]
    md += [f"| {r['user_id']} | {r['origin']} | {r['actual_total']} | {r['predicted_total']} | {r['error']} | {r['baseline_weekday_error']} |" for r in ea["worst_cases_lightgbm"]]
    md += ["", "## 경고", ""] + [f"- {w}" for w in report["warnings"]]
    return "\n".join(md) + "\n"


def train(output=MODEL_DIR):
    ds = load_dataset()
    dates = sorted({_day(x["date"]) for u in ds.by_user.values() for x in u.daily
                    if not x.get("future_scenario") and _day(x["date"]) <= _day(ds.meta["snapshot_date"])})
    if len(dates) < 45:
        raise RuntimeError(f"only_{len(dates)}_observed_calendar_days_need_45")
    # 55 observed days: ~25 training, 15 validation, 15 untouched test.
    validation_start, test_start = dates[-30], dates[-15]
    x, y = _examples(ds, validation_start)
    if len(y) < 100:
        raise RuntimeError(f"insufficient_training_rows:{len(y)}")
    fitted = _fit(x, y)
    model = _Booster(fitted)
    validation, user_validation, _ = _evaluate(ds, {}, model, validation_start, test_start - timedelta(days=1))
    scores = {m: validation[m]["7"]["mae"] for m in METHODS}
    available = {k: v for k, v in scores.items() if v is not None}
    if len(available) < len(METHODS):
        raise RuntimeError("validation_has_no_complete_7day_cases")
    best_baseline = min(("overall", "recent7", "weekday"), key=lambda m: available[m])
    # Do not claim LightGBM outperforms baselines unless held-out validation supports it.
    selected = "lightgbm" if available["lightgbm"] < available[best_baseline] else best_baseline
    test, user_test, records = _evaluate(ds, {}, model, test_start, dates[-1])
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    fitted.booster_.save_model(str(output / "variable_spend.txt"))
    metadata = {
        "version": VERSION, "features": list(FEATURES), "seed": SEED, "params": PARAMS,
        "target": "outflow / user_mean (monetary features scaled by user_mean)",
        "dataset_id": ds.meta["dataset_id"], "synthetic_only": True,
        "training_data_cutoff": (validation_start - timedelta(days=1)).isoformat(),
        "validation_start": validation_start.isoformat(), "test_start": test_start.isoformat(),
        "test_end": dates[-1].isoformat(), "training_samples": len(y),
        "selected_model": selected, "selection_basis": "validation_7day_mae",
        "validation_7day_mae": available,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"metadata": metadata, "validation": validation, "test": test,
              "test_users": user_test, "validation_users": user_validation,
              "metrics": {"validation": _flat_metrics(validation), "test": _flat_metrics(test)},
              "error_analysis": error_analysis(records, ds),
              "warnings": ["55 observed synthetic calendar days; overlapping test origins are not independent",
                           "14-day test can contain only one origin per user (16 cases)",
                           "balance warning metrics compare identical scheduled obligations + realized variable outflows (proxy)",
                           "25% upper scenario is uncalibrated assumption, not a probability or prediction interval",
                           "synthetic evaluation never demonstrates real-user performance"]}
    (output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "evaluation.md").write_text(render_markdown(report), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(MODEL_DIR))
    args = parser.parse_args()
    print(json.dumps(train(Path(args.output)), ensure_ascii=False, indent=2))
