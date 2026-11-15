"""Steps 3-4: LightGBM vs mean baselines, error analysis, and the chart API (real model calls)."""
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
import app.ml.forecast as fc
from app.ml.train import train, _examples, HORIZONS
from app.main import app
from app.services.dataset import load_dataset

client = TestClient(app)
METHODS = {"overall", "recent7", "weekday", "lightgbm"}


@pytest.fixture(scope="module", autouse=True)
def trained(tmp_path_factory):
    out = tmp_path_factory.mktemp("model")
    report = train(output=out)
    mp = pytest.MonkeyPatch()
    mp.setattr(fc, "MODEL_DIR", out)
    yield report
    mp.undo()


def test_report_compares_four_methods_on_three_horizons_with_mae_and_rmse(trained):
    for split in ("validation", "test"):
        rows = trained["metrics"][split]
        assert {(r["method"], r["horizon_days"]) for r in rows} == {(m, h) for m in METHODS for h in HORIZONS}
        assert all(r["mae"] is not None and r["rmse"] >= r["mae"] * 0.99 for r in rows)
        # every method is scored on exactly the same cases
        for h in HORIZONS:
            assert len({r["cases"] for r in rows if r["horizon_days"] == h}) == 1


def test_error_analysis_tables_present(trained):
    ea = trained["error_analysis"]
    assert {r["method"] for r in ea["by_weekday"]} == METHODS and len(ea["by_weekday"]) == 28
    assert {r["spend_level"] for r in ea["by_spend_level"]} >= {"보통(0.5~1.5×)"}
    assert 0 < len(ea["worst_cases_lightgbm"]) <= 10
    assert abs(ea["worst_cases_lightgbm"][0]["error"]) >= abs(ea["worst_cases_lightgbm"][-1]["error"])


def test_training_uses_multi_horizon_rows_and_no_label_at_or_after_cutoff(trained):
    ds = load_dataset()
    meta = trained["metadata"]
    assert meta["training_samples"] > 1500  # h=1..14 rows, not a single 1-day-ahead target
    early = _examples(ds, date(2026, 10, 1))[1]
    late = _examples(ds, date(2026, 10, 6))[1]
    assert 0 < len(early) < len(late) == meta["training_samples"]
    assert meta["training_data_cutoff"] < meta["validation_start"] < meta["test_start"]
    assert meta["selected_model"] in METHODS and set(meta["validation_7day_mae"]) == METHODS


def test_chart_response_shape_and_dates():
    r = client.get("/api/v4/users/S01/forecast-chart", params={"as_of": "2026-11-04", "horizon_days": 14, "lookback_days": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    series = body["series"]
    assert len(series) == 21 and [p["phase"] for p in series] == ["history"] * 7 + ["forecast"] * 14
    days = [date.fromisoformat(p["date"]) for p in series]
    assert all(b - a == timedelta(days=1) for a, b in zip(days, days[1:])) and days[7] == date(2026, 11, 5)
    assert all(p["predicted"] is None and p["actual"] is not None for p in series[:7])
    fcst = series[7:]
    assert all(isinstance(p["predicted"], int) and p["predicted"] >= 0 for p in fcst)
    assert all(p["actual"] is None for p in fcst)  # after the observation cutoff: never filled from oracle rows
    assert {p["method"] for p in fcst} == {"lightgbm"} and body["model_version"].startswith("lgbm")
    assert all(p[k] is not None for p in fcst for k in ("lightgbm", "baseline_overall", "baseline_recent7", "baseline_weekday"))


def test_chart_calls_the_real_lightgbm_model(trained):
    ds = load_dataset(); u = ds.get_user("S02"); as_of = date(2026, 11, 4)
    meta, booster = fc.load_artifact()
    ledger = fc.observed_ledger(u, as_of)
    targets = [as_of + timedelta(days=i) for i in range(1, 8)]
    direct = [max(0, round(float(v))) for v in fc.predict_lightgbm(booster, u, ledger, as_of, targets)]
    body = client.get("/api/v4/users/S02/forecast-chart", params={"horizon_days": 7, "lookback_days": 0}).json()
    assert [p["lightgbm"] for p in body["series"]] == direct == [p["predicted"] for p in body["series"]]


def test_baselines_match_definitions():
    ds = load_dataset(); u = ds.get_user("S03"); as_of = date(2026, 11, 4)
    led = fc.observed_ledger(u, as_of)
    hist = [v["outflow"] for d, v in led.items() if v["outflow"] is not None]
    last7 = [led[as_of - timedelta(days=i)]["outflow"] for i in range(7) if led.get(as_of - timedelta(days=i), {}).get("outflow") is not None]
    p = client.get("/api/v4/users/S03/forecast-chart", params={"horizon_days": 3, "lookback_days": 0}).json()["series"][0]
    assert p["baseline_overall"] == round(sum(hist) / len(hist))
    assert p["baseline_recent7"] == round(sum(last7) / len(last7))


def test_backtest_actuals_and_live_metrics_against_observed_data(trained):
    ds = load_dataset(); u = ds.get_user("S01")
    truth = fc.observed_ledger(u, date(2026, 11, 4))
    body = client.get("/api/v4/users/S01/forecast-chart", params={"as_of": "2026-10-25", "horizon_days": 7}).json()
    fcst = [p for p in body["series"] if p["phase"] == "forecast"]
    for p in fcst:
        expected = truth[date.fromisoformat(p["date"])]["outflow"]
        assert p["actual"] == expected
    lg = next(m for m in body["live_metrics"] if m["model"] == "lightgbm")
    assert lg["days_compared"] == sum(p["actual"] is not None for p in fcst)
    ae = [abs(p["lightgbm"] - p["actual"]) for p in fcst if p["actual"] is not None]
    assert lg["mae"] == round(sum(ae) / len(ae), 2)
    total7 = next(t for t in body["totals"] if t["horizon_days"] == 7)
    assert total7["predicted_total"] == sum(p["predicted"] for p in fcst)


def test_results_depend_on_inputs(trained):
    f = lambda **kw: client.get("/api/v4/users/" + kw.pop("u", "S01") + "/forecast-chart", params={"horizon_days": 7, "lookback_days": 0, **kw}).json()
    a, b, c = f(), f(u="S09"), f(as_of="2026-10-28")
    series = lambda x: [p["predicted"] for p in x["series"]]
    assert series(a) != series(b) and series(a) != series(c)
    w = f(model="weekday")
    assert [p["predicted"] for p in w["series"]] == [p["baseline_weekday"] for p in w["series"]]
    assert {p["method"] for p in w["series"]} == {"weekday"}


def test_lightgbm_before_training_cutoff_is_rejected_but_baseline_works(trained):
    early = {"as_of": "2026-10-02", "horizon_days": 3}
    assert client.get("/api/v4/users/S01/forecast-chart", params=early).status_code == 422
    assert client.get("/api/v4/users/S01/forecast-chart", params={**early, "model": "weekday"}).status_code == 200


def test_chart_errors_and_swagger():
    assert client.get("/api/v4/users/S99/forecast-chart").status_code == 404
    assert client.get("/api/v4/users/S01/forecast-chart", params={"model": "gpt"}).status_code == 422
    assert client.get("/api/v4/users/S01/forecast-chart", params={"as_of": "2027-01-01"}).status_code in (422,)
    spec = client.get("/openapi.json").json()
    assert "/api/v4/users/{user_id}/forecast-chart" in spec["paths"] and "/api/v4/ml/evaluation" in spec["paths"]
    assert spec["components"]["schemas"]["ForecastChartResponse"]["examples"]


def test_evaluation_endpoint_returns_saved_report(trained):
    r = client.get("/api/v4/ml/evaluation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["metrics"]["test"]) == 12 and body["error_analysis"]["by_weekday"]
    assert body["metrics"]["test"] == trained["metrics"]["test"] or len(body["metrics"]["test"]) == 12


def test_evaluation_missing_file_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "MODEL_DIR", tmp_path)
    assert client.get("/api/v4/ml/evaluation").status_code == 404


def test_modified_forecast_endpoint_has_response_model_and_model_param(trained):
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v4/users/{user_id}/forecast"]["get"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ForecastResponse")
    assert spec["components"]["schemas"]["ForecastResponse"]["examples"]
    lg = client.get("/api/v4/users/S01/forecast", params={"model": "lightgbm"}).json()
    wd = client.get("/api/v4/users/S01/forecast", params={"model": "weekday"}).json()
    assert lg["model_type"] == "lightgbm" and wd["model_type"] == "weekday" and lg["daily"] != wd["daily"]
    assert client.get("/api/v4/users/S01/forecast", params={"model": "gpt"}).status_code == 422
