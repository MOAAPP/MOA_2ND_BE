"""Deterministic canonical 20-persona data-quality report; no synthetic repair."""
from __future__ import annotations
import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import median
from app.services.dataset import load_dataset
from app.ml.forecast import observed_ledger, _day


def audit_dataset():
    ds=load_dataset()
    start=_day(ds.meta["start_date"]);end=_day(ds.meta["snapshot_date"])
    user_reports={}
    categories=Counter()
    methods=Counter()
    statuses=Counter()
    anomalous=[]
    distinct_tx=set()
    all_dates=set()
    missing=[]
    for uid,u in sorted(ds.by_user.items()):
        ledger=observed_ledger(u,end)
        complete={d:r for d,r in ledger.items() if r["complete"]}
        incomplete=sorted(d.isoformat() for d,r in ledger.items() if not r["complete"])
        observed={d.isoformat() for d in ledger}
        startdate=min(ledger) if ledger else None
        span=set()
        if startdate:
            from datetime import timedelta
            d=startdate
            while d<=end:
                span.add(d.isoformat());d+=timedelta(days=1)
        absent=sorted(span-observed)
        amounts=[v["outflow"] for v in complete.values()]
        mid=median(amounts) if amounts else 0
        cutoff=max(100000,mid*5)
        big_days=[{"date":d.isoformat(),"outflow":r["outflow"]} for d,r in complete.items()
                  if r["outflow"]>cutoff]
        txs=[]
        future=0
        for t in u.transactions:
            tid=t["id"]
            if tid in distinct_tx:
                anomalous.append({"type":"duplicate_transaction_id","id":tid})
            distinct_tx.add(tid)
            if t.get("future_scenario") or t.get("phase")=="future_scenario":
                future+=1;continue
            if t.get("date","9999")>end.isoformat():continue
            txs.append(t)
            if t.get("kind")=="spend":
                categories[str(t.get("category","unknown"))]+=1
                methods[str(t.get("method","unknown"))]+=1
            statuses[str(t.get("status","unknown"))]+=1
        outflow=sum(amounts)
        paid_credit=sum(t["amount"] for t in txs if t.get("kind")=="card_payment" and isinstance(t.get("amount"),int))
        credit_authorized=sum(t["amount"] for t in txs if t.get("kind")=="spend" and t.get("method")=="credit"
                              and isinstance(t.get("amount"),int))
        user_reports[uid]={
            "display_name":u.profile.get("description",uid),
            "observed_start":startdate.isoformat() if startdate else None,
            "observed_end":end.isoformat(),
            "observed_day_rows":len(ledger),"complete_days":len(complete),
            "incomplete_days":incomplete,"absent_days":absent,
            "confirmed_zero_variable_account_outflow_days":sum(r["outflow"]==0 for r in complete.values()),
            "total_complete_observed_variable_cash_outflow":outflow,
            "large_outflow_days_threshold_rule":f"max(100000,5x median={mid})",
            "large_outflow_days":big_days,
            "transaction_count_observed":len(txs),"transaction_count_future_scenario":future,
            "accounts":len(u.accounts),"schedules":len(u.schedules),
            "scheduled_income":sum(s.get("direction")=="in" for s in u.schedules),
            "scheduled_outflow":sum(s.get("direction")=="out" for s in u.schedules),
            "balance_checks":len(u.balance_checks),
            "investment_quotes":len(u.investments),
            "portfolio_snapshots":len(u.portfolio_snapshots),
            "purchase_intents":len(u.purchase_intents),
            "credit_purchase_authorizations_observed":credit_authorized,
            "actual_card_payments_observed":paid_credit,
            "notes":["incomplete day is missing, not 0 KRW",
                     "credit approval is NOT payment-account outflow",
                     "a large day above a heuristic threshold is a review flag, not a confirmed data error"],
        }
        all_dates.update(observed)
        missing.extend((uid,d) for d in absent)
    return {
        "dataset_id":ds.meta["dataset_id"],"synthetic":ds.meta["synthetic"],
        "declared_observation_window":{"start":str(start),"end":str(end)},
        "user_count":len(ds.by_user),"user_ids":sorted(ds.by_user),
        "transaction_rows_total":len(ds.raw.get("transactions",[])),
        "observed_daily_rows":sum(x["observed_day_rows"] for x in user_reports.values()),
        "categories_transaction_counts":dict(categories),
        "payment_methods_transaction_counts":dict(methods),
        "observed_status_counts":dict(statuses),
        "duplicate_transaction_ids":anomalous,
        "missing_user_date_pairs":missing,
        "per_user":user_reports,
        "warning":["fully synthetic, not a real banking sample",
                   "possible missingness is not assumed to be zero",
                   "future scenario records are not observed labels",
                   "balances are account payment snapshots, not investment value",
                   "outlier flags are heuristic and require review"],
    }


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output",default="app/data/models/dataset_audit.json")
    args=p.parse_args()
    result=audit_dataset()
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"dataset_id":result["dataset_id"],"users":result["user_count"],
                      "daily_rows":result["observed_daily_rows"],
                      "transaction_rows":result["transaction_rows_total"],
                      "missing_user_dates":len(result["missing_user_date_pairs"]),
                      "duplicate_transaction_ids":len(result["duplicate_transaction_ids"])},
                     ensure_ascii=False))
