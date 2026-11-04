"""Stage 4 invariant tests on the exact canonical synthetic ZIP (20 personas)."""
from __future__ import annotations
from datetime import date, timedelta
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.dataset import load_dataset
from app.ml import forecast as ml
from app.ml.train import train

client=TestClient(app)
DATE=date(2026,11,4)


def test_exact_user_ids_and_no_future_oracle_in_training_ledger():
    ds=load_dataset()
    assert set(ds.profiles)=={f"S{i:02}" for i in range(1,21)}
    for uid in ds.profiles:
        u=ds.get_user(uid)
        ledger=ml.observed_ledger(u,DATE)
        assert len(ledger)<=55
        assert all(d<=DATE for d in ledger)
        assert all(v["outflow"] is None for v in ledger.values() if not v["complete"])
        assert all(v["outflow"]>=0 for v in ledger.values() if v["complete"])
    assert ds.meta["synthetic"] is True


def test_earlier_forecast_never_looks_at_later_transactions():
    ds=load_dataset()
    u=ds.get_user("S01")
    early=date(2026,10,15)
    ledger=ml.observed_ledger(u,DATE)
    future_modified=dict(ledger)
    for d in list(future_modified):
        if d>early:
            future_modified[d]={**future_modified[d],"outflow":999999999}
    f1=ml.feature_row(u,ledger,early,early+timedelta(days=1))
    f2=ml.feature_row(u,future_modified,early,early+timedelta(days=1))
    assert f1==f2
    for target in (early+timedelta(days=1),early+timedelta(days=7)):
        assert ml.baseline_daily(ledger,early,target,"weekday")==ml.baseline_daily(future_modified,early,target,"weekday")


def test_credit_approval_not_account_variable_outflow_and_missing_not_zero():
    ds=load_dataset()
    u=ds.get_user("S06")
    ledger=ml.observed_ledger(u,DATE)
    assert any(x["credit"]>0 for x in ledger.values())
    assert any(x["outflow"] is None for x in ml.observed_ledger(ds.get_user("S04"),DATE).values())
    # Payment-account variable outflow excludes authorization and cash-wallet spend.
    d=next((d for d,x in ledger.items() if x["credit"]>0),None)
    assert d is not None
    assert ledger[d]["outflow"] is None or ledger[d]["outflow"]>=0


def test_baseline_api_and_deterministic_forecast_identity(monkeypatch,tmp_path):
    monkeypatch.setattr(ml,"MODEL_DIR",tmp_path/"missing-model")
    ds=load_dataset()
    a=ml.run_forecast(ds,"S01",DATE,14)
    b=ml.run_forecast(ds,"S01",DATE,14)
    assert a==b
    assert a["model_type"]=="weekday"
    assert len(a["daily"])==14
    assert a["forecasts"][2]["predicted_variable_outflow"]==sum(r["amount"] for r in a["daily"])
    assert not a["candidate_purchases_included"]
    assert len({e.id for e in ml.to_events(ds.get_user("S01"),a)})==14
    assert all(e.status=="predicted" and e.source=="rules" for e in ml.to_events(ds.get_user("S01"),a))


def test_insufficient_observation_is_truthfully_unavailable(monkeypatch,tmp_path):
    monkeypatch.setattr(ml,"MODEL_DIR",tmp_path/"missing-model")
    ds=load_dataset()
    first=date.fromisoformat(ds.meta["start_date"])
    p=ml.run_forecast(ds,"S01",first,14)
    assert p["model_type"]=="unavailable"
    assert p["data_quality_status"]=="insufficient"
    assert p["forecasts"]==[]


def test_real_lgbm_training_artifact_validation_and_test_are_separate(tmp_path,monkeypatch):
    report=train(output=tmp_path)
    meta=report["metadata"]
    assert meta["version"]==ml.VERSION
    assert meta["training_samples"]>=100
    assert meta["training_data_cutoff"]<meta["validation_start"]<meta["test_start"]
    assert meta["test_end"]=="2026-11-04"
    assert (tmp_path/"variable_spend.txt").is_file()
    assert (tmp_path/"metadata.json").is_file()
    assert (tmp_path/"evaluation.json").is_file()
    assert meta["selected_model"] in ("lightgbm","weekday","recent7")
    assert report["test"]["lightgbm"]["7"]["cases"]>0
    assert len(report["test_users"])==20
    monkeypatch.setattr(ml,"MODEL_DIR",tmp_path)
    a=ml.run_forecast(load_dataset(),"S01",DATE)
    b=ml.run_forecast(load_dataset(),"S01",DATE)
    assert a==b
    assert a["model_type"]==meta["selected_model"]
    first=ml.run_forecast(load_dataset(),"S01",date(2026,9,11))
    assert first["model_type"]=="unavailable" and first["forecasts"]==[]
    response=client.get("/api/v4/users/S01/forecast?as_of=2026-11-04")
    assert response.status_code==200,response.text
    assert response.json()["forecast_run_id"]==a["forecast_run_id"]
    cash=client.get("/api/v4/users/S01/forecast-cashflow?as_of=2026-11-04")
    assert cash.status_code==200,cash.text
    assert cash.json()["cashflow"]["forecast_applied"] is True
    assert len(cash.json()["cashflow"]["daily_cashflow"])==14
    home=client.get("/api/v4/users/S01/home?as_of=2026-11-04")
    assert home.status_code==200,home.text
    assert home.json()["variable_forecast"]["forecast_run_id"]==a["forecast_run_id"]
    assert home.json()["future_balances"]["14"]==cash.json()["cashflow"]["future_balances"]["14"]


def test_all_twenty_home_users_and_date_change():
    ds=load_dataset()
    for uid in sorted(ds.profiles):
        r=client.get(f"/api/v4/users/{uid}/home?as_of=2026-11-04")
        assert r.status_code==200,(uid,r.text)
        d=r.json()
        assert d["user_id"]==uid
        assert d["current_account_id"]==f"{uid}-PAY"
        assert all(t["user_id"]==uid for t in d["recent_transactions"])
    r=client.get("/api/v4/users/S01/home?as_of=2026-11-03")
    assert r.status_code==200
    assert all(t["date"]<="2026-11-03" for t in r.json()["recent_transactions"])
    assert client.get("/api/v4/users/S99/forecast").status_code==404
    assert client.get("/api/v4/users/S01/home?as_of=2026-11-05").status_code==422


def test_data_quality_report_twenty_personas():
    from app.ml.audit import audit_dataset
    report=audit_dataset()
    assert report['user_count']==20
    assert report['user_ids']==[f'S{i:02}' for i in range(1,21)]
    assert report['transaction_rows_total']==12777
    assert not report['duplicate_transaction_ids']
    assert len(report['per_user'])==20
    assert all(u['observed_day_rows']<=55 for u in report['per_user'].values())
    assert all(u['confirmed_zero_variable_account_outflow_days']>=0 for u in report['per_user'].values())
