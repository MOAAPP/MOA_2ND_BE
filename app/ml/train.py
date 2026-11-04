"""Offline train/validation/test by time. Never retrain inside API requests."""
from __future__ import annotations
import argparse
import json
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from app.ml.forecast import (
    FEATURES, SEED, VERSION, MODEL_DIR, MIN_HISTORY, observed_ledger,
    feature_row, baseline_daily, run_forecast, _day, _quality,
)
from app.services.dataset import load_dataset


def _examples(ds, train_until, val_until):
    from app.ml.forecast import ForecastProblem
    train_x=[];train_y=[]
    for uid in sorted(ds.profiles):
        u=ds.get_user(uid)
        ledger=observed_ledger(u,_day(ds.meta["snapshot_date"]))
        dates=sorted(ledger)
        for origin in dates[:-1]:
            target=origin+timedelta(days=1)
            if target not in ledger or ledger[target]["outflow"] is None:continue
            if origin>=train_until:continue
            try:
                row=feature_row(u,ledger,origin,target)
            except ForecastProblem:continue
            train_x.append(row);train_y.append(ledger[target]["outflow"])
    return np.asarray(train_x,dtype=float),np.asarray(train_y,dtype=float)


def _evaluate(ds, meta, model, first, last):
    """All models see identical complete future target dates within one period."""
    from app.ml.forecast import ForecastProblem
    errors=defaultdict(list); per_user=defaultdict(lambda:defaultdict(list))
    balances=defaultdict(list); missed=defaultdict(int); false_warning=defaultdict(int)
    upper=defaultdict(list); origins=defaultdict(set)
    for uid in sorted(ds.profiles):
        u=ds.get_user(uid)
        ledger=observed_ledger(u,_day(ds.meta["snapshot_date"]))
        for origin in sorted(d for d in ledger if first<=d<=last):
            try:
                feature_row(u,ledger,origin,origin+timedelta(days=1))
            except ForecastProblem:continue
            for h in (3,7,14):
                target_dates=[origin+timedelta(days=i) for i in range(1,h+1)]
                if target_dates[-1]>last or any(d not in ledger or ledger[d]["outflow"] is None for d in target_dates):
                    continue
                true_daily=[ledger[d]["outflow"] for d in target_dates]
                truth=sum(true_daily)
                forecasts={}
                for method in ("recent7","weekday","lightgbm"):
                    if method=="lightgbm":
                        vals=[max(0,round(float(model.predict([feature_row(u,ledger,origin,d)])[0]))) for d in target_dates]
                    else:
                        vals=[max(0,round(baseline_daily(ledger,origin,d,method))) for d in target_dates]
                    forecasts[method]=vals
                    errors[(method,h)].append(abs(truth-sum(vals)))
                    per_user[(uid,method,h)]["mae"].append(abs(truth-sum(vals)))
                    upper[(method,h)].append(int(truth<=sum(round(x*1.25) for x in vals)))
                origins[(uid,h)].add(origin.isoformat())
                # Financial risk proxy, with identical scheduled obligations and oracle
                # variable cash only. Not a bank-balance ground truth reconstruction.
                from app.schemas import CalculationRequest
                from app.services.finance import calculate
                from app.ml.forecast import to_events
                for method, vals in forecasts.items():
                    f={"user_id":uid,"forecast_run_id":f"eval:{uid}:{origin}:{method}",
                       "model_type":method,"daily":[{"date":d.isoformat(),"amount":v}
                         for d,v in zip(target_dates,vals)]}
                    truth_f={**f,"daily":[{"date":d.isoformat(),"amount":v}
                                             for d,v in zip(target_dates,true_daily)]}
                    truth_out=calculate(ds,CalculationRequest(user_id=uid,as_of=origin,horizon_days=h,
                                                               forecast_events=to_events(u,truth_f)))
                    predicted=calculate(ds,CalculationRequest(user_id=uid,as_of=origin,horizon_days=h,
                                                               forecast_events=to_events(u,f)))
                    balances[(method,h)].append(abs(predicted.future_balances[str(h)]-truth_out.future_balances[str(h)]))
                    if truth_out.minimum_balance<0<=predicted.minimum_balance:
                        missed[(method,h)]+=1
                    if predicted.minimum_balance<0<=truth_out.minimum_balance:
                        false_warning[(method,h)]+=1
    summary={}
    for method in ("recent7","weekday","lightgbm"):
        summary[method]={}
        for h in (3,7,14):
            key=(method,h);e=errors[key]
            summary[method][str(h)]={
                "cases":len(e),"mae":round(sum(e)/len(e),2) if e else None,
                "balance_mae_proxy":round(sum(balances[key])/len(balances[key]),2) if balances[key] else None,
                "missed_shortfalls_proxy":missed[key],"false_warnings_proxy":false_warning[key],
                "upper_25pct_coverage_assumption":round(sum(upper[key])/len(upper[key]),4) if upper[key] else None,
            }
    users={uid:{"cases":{str(h):len(origins[(uid,h)]) for h in (3,7,14)},
                "mae_7":{m:round(float(np.mean(per_user[(uid,m,7)]["mae"])),2)
                          if per_user[(uid,m,7)]["mae"] else None
                          for m in ("recent7","weekday","lightgbm")}}
           for uid in sorted(ds.profiles)}
    return summary,users


def train(output=MODEL_DIR):
    import lightgbm as lgb
    ds=load_dataset()
    dates=sorted({_day(x["date"]) for u in ds.by_user.values() for x in u.daily
                  if not x.get("future_scenario") and _day(x["date"])<=_day(ds.meta["snapshot_date"])})
    if len(dates)<45: raise RuntimeError(f"only_{len(dates)}_observed_calendar_days_need_45")
    # 55 observed days: 25 training, 15 validation, 15 untouched test.
    validation_start=dates[-30]
    test_start=dates[-15]
    x,y=_examples(ds,validation_start,test_start)
    if len(y)<100: raise RuntimeError(f"insufficient_training_rows:{len(y)}")
    model=lgb.LGBMRegressor(objective="regression_l1",n_estimators=125,
                           num_leaves=7,max_depth=4,min_child_samples=15,
                           learning_rate=0.045,reg_lambda=12,random_state=SEED,
                           n_jobs=1,verbosity=-1)
    model.fit(x,y)
    validation,user_validation=_evaluate(ds,{},model,validation_start,test_start-timedelta(days=1))
    scores={method:validation[method]["7"]["mae"] for method in ("recent7","weekday","lightgbm")}
    available={k:v for k,v in scores.items() if v is not None}
    if len(available)<3:raise RuntimeError("validation_has_no_complete_7day_cases")
    baseline=min(("recent7","weekday"),key=lambda m:available[m])
    # Do not claim LightGBM outperforms baseline unless held-out validation supports it.
    selected="lightgbm" if available["lightgbm"]<available[baseline] else baseline
    test,user_test=_evaluate(ds,{},model,test_start,dates[-1])
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    model.booster_.save_model(str(output/"variable_spend.txt"))
    metadata={
        "version":VERSION,"features":list(FEATURES),"seed":SEED,
        "dataset_id":ds.meta["dataset_id"],"synthetic_only":True,
        "training_data_cutoff":(validation_start-timedelta(days=1)).isoformat(),
        "validation_start":validation_start.isoformat(),"test_start":test_start.isoformat(),
        "test_end":dates[-1].isoformat(),"training_samples":len(y),
        "selected_model":selected,"selection_basis":"validation_7day_mae",
    }
    (output/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    report={"metadata":metadata,"validation":validation,"test":test,
            "test_users":user_test,"validation_users":user_validation,
            "warnings":["55 observed synthetic calendar days; overlapping test origins are not independent",
                        "14-day test can contain only one origin per user",
                        "balance warning metrics compare identical scheduled obligations + realized variable outflows (proxy)",
                        "25% upper scenario is uncalibrated assumption, not a probability or prediction interval",
                        "synthetic evaluation never demonstrates real-user performance"]}
    (output/"evaluation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default=str(MODEL_DIR))
    args=parser.parse_args()
    print(json.dumps(train(Path(args.output)),ensure_ascii=False,indent=2))
