"""Stage-1 tests against the attached REAL synthetic dataset, plus isolated unit fixtures.
Synthetic edge-case fixtures never enter the source dataset or application state.
"""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zipfile import ZipFile
from collections import defaultdict
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas import CalculationRequest, CashEvent, ExternalEvent
from app.services.dataset import Dataset, DataProblem, UserData, load_dataset
from app.services.finance import (
    KST, anchor_for, calculate, credit_bills, eod, normalise_observed, recorded_events,
    scheduled_events, simulate_days, state_for, next_month, FinanceProblem,
)


@pytest.fixture(scope='module')
def dataset():
    return load_dataset()


def event(name, day, amount, *, essential=False, hour=12):
    return CashEvent(id=name, user_id='TST', account_id='TST-PAY',
                     occurred_at=datetime(2026, 11, 4, 8, tzinfo=KST),
                     debit_at=datetime(2026, 11, day, hour, tzinfo=KST), amount=amount,
                     kind='fixed_expense' if essential else 'spend', payment_method='debit',
                     transaction_status='simulated', transaction_source='fixture', essential=essential,
                     label=name)


def fixture_user(txns=(), schedules=(), snapshots=()):
    return UserData(user_id='TST', profile={'payment_account_id':'TST-PAY','reserve_account_id':'TST-RESERVE'},
                    accounts=({'user_id':'TST','account_id':'TST-PAY','opening_balance':100000},),
                    transactions=tuple(txns), schedules=tuple(schedules), balance_checks=(), daily=tuple(snapshots),
                    pending_card_liabilities=(), investments=(), portfolio_snapshots=(), purchase_intents=())


def txn(name, kind='spend', method='debit', amount=5000, when=None, debit=None, **kw):
    when = when or datetime(2026, 11, 4, 12, tzinfo=KST)
    debit = debit if debit is not None else when
    return dict(id=name, user_id='TST', kind=kind, method=method,
                amount=amount, account_id='TST-PAY' if method!='credit' else 'TST-CR1',
                occurred_at=int(when.timestamp()*1000), debit_at=int(debit.timestamp()*1000),
                status='confirmed_synthetic', source='synthetic', phase='historical_synthetic',
                future_scenario=False, name=name, essential=False, **kw)


def test_dataset_loading_and_manifest(dataset):
    assert len(dataset.profiles) == 20
    assert dataset.meta['snapshot_date'] == '2026-11-04'
    assert len(dataset.raw['transactions']) == 12777
    assert not dataset.meta['contains_real_personal_financial_data']


def test_original_is_read_only(dataset):
    a = dataset.get_user('S01').profile['opening_balance']
    copy = dict(dataset.get_user('S01').profile); copy['opening_balance'] = -1
    assert dataset.get_user('S01').profile['opening_balance'] == a


def test_user_isolation(dataset):
    a = dataset.get_user('S01'); b = dataset.get_user('S02')
    assert all(t['user_id'] == 'S01' for t in a.transactions)
    assert all(t['user_id'] == 'S02' for t in b.transactions)
    assert set(t['id'] for t in a.transactions).isdisjoint(t['id'] for t in b.transactions)
    assert calculate(dataset,CalculationRequest(user_id='S01')).account_id == 'S01-PAY'
    with pytest.raises(FinanceProblem):
        calculate(dataset,CalculationRequest(user_id='S01',account_id='S02-PAY'))


def test_snapshot_eod_matches_dataset(dataset):
    for uid in dataset.profiles:
        r = calculate(dataset,CalculationRequest(user_id=uid))
        expected = next(x['account_balance_eod'] for x in dataset.get_user(uid).daily if x['date']=='2026-11-04')
        assert r.available_cash == expected
        assert r.balance_snapshot_at == eod(date(2026,11,4))
        assert r.balance_snapshot_source == 'observed_synthetic_daily_eod'


def test_oracle_future_not_used(dataset):
    r = calculate(dataset,CalculationRequest(user_id='S01'))
    assert r.excluded_oracle_transaction_count > 0
    ids = {e.id for day in r.daily_cashflow for e in day.events}
    assert 'txn:S01-T00444' not in ids  # future settlement oracle
    assert 'credit_bill:S01-CR1:2026-11-14' in ids  # independently from observed approvals
    assert not r.forecast_applied
    assert 'no_ml_predictions_provided' in r.warnings


def test_credit_approval_settlement_once_and_aggregate():
    card = txn('credit1', method='credit', amount=30000,
               debit=datetime(2026,11,14,14,tzinfo=KST),card_id='TST-CR1')
    a = datetime(2026,11,4,23,59,59,tzinfo=KST)
    u = fixture_user([card]); known, _ = normalise_observed(u.transactions,a)
    assert recorded_events(u,'TST-PAY',known,a,eod(date(2026,11,20)),[]) == []
    bills = credit_bills(u,'TST-PAY',known,a,eod(date(2026,11,20)),[])
    assert len(bills)==1 and bills[0].amount==-30000
    days,_=simulate_days(a,100000,bills,14)
    assert days[9].closing_balance==70000


def test_confirmed_card_payment_overrides_approval():
    credit = txn('credit1', method='credit',amount=30000,
                 debit=datetime(2026,11,14,14,tzinfo=KST),card_id='TST-CR1')
    payment = txn('payment', kind='card_payment', method='transfer',amount=29000,
                  when=datetime(2026,11,1,12,tzinfo=KST),
                  debit=datetime(2026,11,14,14,tzinfo=KST),card_id='TST-CR1')
    u=fixture_user([credit,payment]);a=eod(date(2026,11,4))
    known,_=normalise_observed(u.transactions,a)
    assert credit_bills(u,'TST-PAY',known,a,eod(date(2026,11,20)),[])==[]
    actual=recorded_events(u,'TST-PAY',known,a,eod(date(2026,11,20)),[])
    assert len(actual)==1 and actual[0].amount==-29000


def test_installment_monthly_legs_and_sum():
    a=eod(date(2026,11,4))
    approved=txn('installment', method='credit', amount=100001, installments=3,
                 debit=datetime(2026,11,14,14,tzinfo=KST),card_id='TST-CR1')
    u=fixture_user([approved]);known,_=normalise_observed(u.transactions,a);warn=[]
    bills=credit_bills(u,'TST-PAY',known,a,eod(date(2027,2,14)),warn)
    assert [(x.debit_at.date(),x.amount) for x in bills] == [
        (date(2026,11,14),-33333),(date(2026,12,14),-33333),(date(2027,1,14),-33335)]
    assert sum(x.amount for x in bills)==-100001
    assert any('installment_schedule_derived' in s for s in warn)


def test_refund_cancel_and_cash_withdrawal_are_account_events():
    a=eod(date(2026,11,4))
    original=txn('purchase',amount=15000,when=datetime(2026,11,4,12,tzinfo=KST),
                 debit=datetime(2026,11,6,12,tzinfo=KST))
    refund=txn('refund',kind='refund',amount=15000,when=datetime(2026,11,4,13,tzinfo=KST),
               debit=datetime(2026,11,7,12,tzinfo=KST),refund_of='purchase')
    atm=txn('atm',kind='atm_withdrawal',method='debit',amount=10000,when=datetime(2026,11,4,14,tzinfo=KST),
            debit=datetime(2026,11,8,12,tzinfo=KST))
    cash=txn('cash_spend',method='cash',amount=2500,when=datetime(2026,11,4,15,tzinfo=KST),
             debit=datetime(2026,11,8,15,tzinfo=KST))
    cash['account_id']='WALLET'
    u=fixture_user([original,refund,atm,cash]);known,_=normalise_observed(u.transactions,a)
    ev=recorded_events(u,'TST-PAY',known,a,eod(date(2026,11,20)),[])
    assert sum(x.amount for x in ev)==-10000
    assert len(ev)==3


def test_self_transfer_not_income_or_spend():
    t=txn('transfer',kind='transfer_self',method='transfer',amount=20000, direction='in',
          source_account_id='TST-RESERVE',
          debit=datetime(2026,11,5,12,tzinfo=KST))
    u=fixture_user([t]);a=eod(date(2026,11,4));known,_=normalise_observed(u.transactions,a)
    ev=recorded_events(u,'TST-PAY',known,a,eod(date(2026,11,20)),[])
    assert len(ev)==1 and ev[0].amount==20000 and ev[0].kind=='transfer_self'
    reserve_ev=recorded_events(u,'TST-RESERVE',known,a,eod(date(2026,11,20)),[])
    assert reserve_ev[0].amount==-20000
    assert sum(x.amount for x in ev+reserve_ev)==0


def test_delayed_salary_projection(dataset):
    normal=calculate(dataset,CalculationRequest(user_id='S01'))
    delayed=calculate(dataset,CalculationRequest(user_id='S01',income_delay_days=5))
    assert normal.future_balances['7'] > delayed.future_balances['7']
    assert normal.future_balances['14'] == delayed.future_balances['14']


def test_future_due_dates_minimum_balance(dataset):
    r=calculate(dataset,CalculationRequest(user_id='S01',minimum_required_balance=20000))
    assert r.minimum_cash_cushion == r.minimum_balance-20000
    assert r.daily_cashflow[0].date==date(2026,11,5)
    assert sum(e.amount for d in r.daily_cashflow for e in d.events if e.id.startswith('schedule:S01-sub:'))==-17000
    assert r.essential_payments_covered==all(p['covered'] for p in r.essential_payment_results)


def test_intra_day_low_and_event_equals_anchor_not_reapplied():
    as_of=datetime(2026,11,4,23,59,59,tzinfo=KST)
    same=CashEvent(**{**event('already_posted',5,-9000).model_dump(), 'debit_at':as_of})
    bills=[event('rent',5,-45000,essential=True),event('salary',5,40000)]
    days,pays=simulate_days(as_of,50000,[same]+bills,3,minimum=10000)
    assert days[0].lowest_balance==5000
    assert days[0].closing_balance==45000
    assert pays[0]['covered']==False
    assert days[0].outflow==45000


def test_virtual_assets_not_real_cash(dataset):
    r=calculate(dataset,CalculationRequest(user_id='S01'))
    virtual=r.virtual_investments
    assert virtual['actual_payment_account_impact']==0
    assert virtual['status']=='simulated_only'
    assert r.available_cash==27800


def test_missing_fields_explicit_warnings(dataset):
    r=calculate(dataset,CalculationRequest(user_id='S01'))
    assert r.savings_goals==[]
    assert any('savings_goals_not_present' in w for w in r.warnings)
    assert r.minimum_required_balance is None


def test_identical_input_deterministic(dataset):
    req=CalculationRequest(user_id='S10', horizon_days=14, minimum_required_balance=50000)
    a=calculate(dataset,req).model_dump(mode='json')
    b=calculate(dataset,req).model_dump(mode='json')
    assert a==b


def test_forecast_separate_and_user_mismatch(dataset):
    when=datetime(2026,11,7,10,tzinfo=KST)
    event=ExternalEvent(id='ml-forecast-1',user_id='S01',account_id='S01-PAY',
                        debit_at=when,amount=-10000,label='ML 변동 지출',kind='variable_spend',
                        status='predicted',source='model',forecast_run_id='model-run-1')
    req=CalculationRequest(user_id='S01',forecast_events=[event])
    base=calculate(dataset,CalculationRequest(user_id='S01'))
    with_ml=calculate(dataset,req)
    assert with_ml.future_balances['3']==base.future_balances['3']-10000
    assert with_ml.forecast_applied
    assert not base.forecast_applied
    bad=event.model_copy(update={'user_id':'S02'})
    with pytest.raises(FinanceProblem):calculate(dataset,CalculationRequest(user_id='S01',forecast_events=[bad]))


def test_simulation_immutable(dataset):
    event=ExternalEvent(id='buy',user_id='S01',account_id='S01-PAY',
                        debit_at=datetime(2026,11,6,10,tzinfo=KST),amount=-89000,label='운동화',
                        kind='purchase',status='simulated',source='simulation',payment_method='debit')
    base=calculate(dataset,CalculationRequest(user_id='S01'))
    changed=calculate(dataset,CalculationRequest(user_id='S01',simulation_events=[event]))
    fresh=calculate(dataset,CalculationRequest(user_id='S01'))
    assert changed.future_balances['3']==base.future_balances['3']-89000
    assert fresh==base
    assert all(t['id']!='buy' for t in dataset.get_user('S01').transactions)


def test_missing_dataset_fails_no_fallback(tmp_path):
    with pytest.raises(DataProblem): Dataset(tmp_path/'none.zip')


def test_api_response_and_docs(dataset):
    client=TestClient(app)
    assert client.get('/openapi.json').status_code==200
    assert client.get('/api/v4/health').json()['users']==20
    assert client.get('/api/v4/users/S01/state').json()['available_cash']==27800
    resp=client.get('/api/v4/users/S01/cashflow?horizon_days=14')
    assert resp.status_code==200
    assert resp.json()['future_balances']['14']==347100
    assert client.get('/api/v4/users/S99/state').status_code==404
    assert client.get('/api/v4/users/S01/cashflow?as_of=2026-11-10').status_code==422
    assert client.get('/api/v4/users/S01/cashflow?account_id=S02-PAY').status_code==422


def test_api_simulate_payload(dataset):
    client=TestClient(app)
    payload={'user_id':'S01','minimum_required_balance':10000,
             'simulation_events':[{'id':'purchase-from-ui','user_id':'S01','account_id':'S01-PAY',
                                   'debit_at':'2026-11-08T12:00:00+09:00','amount':-15000,
                                   'label':'가상 구매','kind':'purchase','status':'simulated','source':'simulation'}]}
    response=client.post('/api/v4/cashflow/simulate',json=payload)
    assert response.status_code==200,response.text
    base=client.get('/api/v4/users/S01/cashflow').json()
    assert response.json()['future_balances']['7']==base['future_balances']['7']-15000


def test_short_horizon_minimum_cushion_matches_returned_low(dataset):
    result = calculate(dataset, CalculationRequest(user_id='S01', horizon_days=3, minimum_required_balance=20000))
    assert len(result.daily_cashflow) == 3
    assert result.minimum_cash_cushion == result.minimum_balance - 20000
    assert result.minimum_balance == min([result.available_cash] + [d.lowest_balance for d in result.daily_cashflow])


@pytest.mark.parametrize('origin', ['http://localhost:5173', 'http://127.0.0.1:5173'])
def test_local_react_cors_get_and_post_preflight(origin):
    """Direct requests from either local Vite origin, including JSON POST preflight."""
    client = TestClient(app)
    response = client.get('/api/v4/health', headers={'Origin': origin})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == origin

    preflight = client.options(
        '/api/v4/cashflow/simulate',
        headers={
            'Origin': origin,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type',
        },
    )
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers['access-control-allow-origin'] == origin
    assert 'POST' in preflight.headers['access-control-allow-methods']
    assert 'content-type' in preflight.headers['access-control-allow-headers'].lower()


def test_unapproved_origin_has_no_cors_access():
    client = TestClient(app)
    response = client.get('/api/v4/health', headers={'Origin': 'https://untrusted.example'})
    assert response.status_code == 200
    assert 'access-control-allow-origin' not in response.headers

def test_swagger_root_and_openapi_discovery():
    """Root should guide local developers to usable Swagger instead of a 404."""
    client = TestClient(app)
    root = client.get('/', follow_redirects=False)
    assert root.status_code == 307
    assert root.headers['location'] == '/docs'

    docs = client.get('/docs')
    assert docs.status_code == 200
    assert 'swagger-ui' in docs.text.lower()

    redoc = client.get('/redoc')
    assert redoc.status_code == 200
    assert 'redoc' in redoc.text.lower()

    openapi = client.get('/openapi.json')
    assert openapi.status_code == 200
    data = openapi.json()
    assert data['info']['title'] == '평행통장 V4 · 금융 계산 API'
    assert '/api/v4/health' in data['paths']
    assert '/api/v4/users/{user_id}/cashflow' in data['paths']
    assert '/api/v4/cashflow/simulate' in data['paths']
    assert data['paths']['/api/v4/cashflow/simulate']['post']['summary'] == '가상 구매·소득 변동 시나리오 비교'


# ---- Step 1 regression: no double debit when payments are slightly early; month-end schedules ----
def _salary_or_rent(day=10, direction='out', **kw):
    return dict({'id': 'rent', 'account_id': 'TST-PAY', 'direction': direction, 'amount': 300000,
                 'recurrence': 'monthly', 'day': day, 'kind': 'rent', 'name': '월세',
                 'status': 'scheduled', 'essential': True}, **kw)


def test_early_card_payment_not_double_debited_with_estimated_bill():
    a = eod(date(2026, 11, 4)); end = eod(date(2026, 11, 20))
    credit = txn('credit1', method='credit', amount=30000, debit=datetime(2026, 11, 14, 14, tzinfo=KST), card_id='TST-CR1')
    pay = txn('payment', kind='card_payment', method='transfer', amount=30000,
              when=datetime(2026, 11, 4, 9, tzinfo=KST), debit=datetime(2026, 11, 12, 9, tzinfo=KST), card_id='TST-CR1')
    u = fixture_user([credit, pay]); known, _ = normalise_observed(u.transactions, a)
    assert credit_bills(u, 'TST-PAY', known, a, end, []) == []
    assert sum(e.amount for e in recorded_events(u, 'TST-PAY', known, a, end, [])) == -30000


def test_card_payment_of_other_card_or_other_month_does_not_cancel_bill():
    a = eod(date(2026, 11, 4)); end = eod(date(2026, 12, 20))
    credit = txn('credit1', method='credit', amount=30000, debit=datetime(2026, 11, 14, 14, tzinfo=KST), card_id='TST-CR1')
    other_card = txn('p1', kind='card_payment', method='transfer', amount=9000, when=datetime(2026, 11, 3, tzinfo=KST),
                     debit=datetime(2026, 11, 14, 9, tzinfo=KST), card_id='TST-CR2')
    last_month = txn('p2', kind='card_payment', method='transfer', amount=9000, when=datetime(2026, 10, 14, tzinfo=KST),
                     debit=datetime(2026, 10, 14, 9, tzinfo=KST), card_id='TST-CR1')
    u = fixture_user([credit, other_card, last_month]); known, _ = normalise_observed(u.transactions, a)
    assert [b.amount for b in credit_bills(u, 'TST-PAY', known, a, end, [])] == [-30000]


def test_schedule_paid_early_is_not_projected_again():
    a = eod(date(2026, 11, 4)); end = eod(date(2026, 11, 20))
    early = txn('rentpay', amount=300000, when=datetime(2026, 11, 3, 10, tzinfo=KST), schedule_id='rent', method='transfer')
    u = fixture_user([early], [_salary_or_rent()]); known, _ = normalise_observed(u.transactions, a)
    assert scheduled_events(u, 'TST-PAY', known, a, end, False, 0, []) == []
    # without that payment the same schedule IS projected (input drives the result)
    u2 = fixture_user([], [_salary_or_rent()])
    assert [e.debit_at.date() for e in scheduled_events(u2, 'TST-PAY', [], a, end, False, 0, [])] == [date(2026, 11, 10)]


@pytest.mark.parametrize('start,stop,expected', [
    (date(2027, 1, 31), date(2027, 5, 1), ['2027-02-28', '2027-03-31', '2027-04-30']),
    (date(2028, 1, 31), date(2028, 3, 5), ['2028-02-29']),
])
def test_month_end_schedule_clamps_to_last_day(start, stop, expected):
    u = fixture_user([], [_salary_or_rent(day=31)])
    got = scheduled_events(u, 'TST-PAY', [], eod(start), eod(stop), False, 0, [])
    assert [e.debit_at.date().isoformat() for e in got] == expected


def test_salary_delay_shifts_payday_and_can_cross_month_end():
    sal = _salary_or_rent(day=31, direction='in', kind='salary', id='sal', name='급여', amount=1000000)
    u = fixture_user([], [sal])
    base = scheduled_events(u, 'TST-PAY', [], eod(date(2026, 10, 30)), eod(date(2026, 11, 20)), False, 0, [])
    late = scheduled_events(u, 'TST-PAY', [], eod(date(2026, 10, 30)), eod(date(2026, 11, 20)), False, 5, [])
    assert [e.debit_at.date() for e in base] == [date(2026, 10, 31)]
    assert [e.debit_at.date() for e in late] == [date(2026, 11, 5)]


def test_real_dataset_card_bill_and_balance_reconcile_with_daily_eod(dataset):
    """Every observed EOD-to-EOD balance change equals the engine's cash effects (no double/missed debit)."""
    from app.services.finance import _cash_effect, kst
    snap = eod(date.fromisoformat(dataset.meta['snapshot_date']))
    for uid in sorted(dataset.profiles):
        u = dataset.get_user(uid); acct = u.profile['payment_account_id']
        obs, _ = normalise_observed(u.transactions, snap)
        by_day = defaultdict(int)
        for t in obs:
            eff = _cash_effect(t, acct, u)
            if eff is not None:
                by_day[kst(t.get('debit_at') or t['occurred_at']).date()] += eff
        rows = sorted((r for r in u.daily if r.get('observation_complete') and r.get('account_balance_eod') is not None),
                      key=lambda r: r['date'])
        for p, n in zip(rows, rows[1:]):
            if (date.fromisoformat(n['date']) - date.fromisoformat(p['date'])).days == 1:
                assert n['account_balance_eod'] - p['account_balance_eod'] == by_day[date.fromisoformat(n['date'])], (uid, n['date'])
