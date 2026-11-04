"""Deterministic KRW cashflow engine, independent of FastAPI and LLM/ML.

- The observed daily EOD balance is an account snapshot: do not replay earlier transactions.
- Credit authorizations are NOT cash debits. Only their later, grouped bill is.
- Dataset future_scenario rows are oracle-only and never enter base forecasts.
- Simulation/model events are caller-provided, never saved back to raw JSON.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from calendar import monthrange
from zoneinfo import ZoneInfo
from app.schemas import CalculationRequest, CashEvent, DailyCashflow, FinancialResult, UserState
from app.services.dataset import Dataset, UserData

KST = ZoneInfo('Asia/Seoul')


class FinanceProblem(ValueError):
    pass


def kst(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, KST)


def eod(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59, 999000), KST)


def midday(d: date) -> datetime:
    return datetime.combine(d, time(12, 0), KST)


def next_month(d: date, months: int) -> date:
    n = d.year * 12 + d.month - 1 + months
    y, m = divmod(n, 12)
    return date(y, m + 1, min(d.day, monthrange(y, m + 1)[1]))


def month_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def normalise_observed(txns: tuple[dict, ...], as_of: datetime) -> tuple[list[dict], int]:
    """Exclude scenario/planned/oracle transactions before any aggregation."""
    observed = []
    excluded = 0
    for row in txns:
        if row.get('future_scenario') or row.get('synthetic_oracle_only') or row.get('phase') == 'future_scenario' or row.get('status') == 'scenario_planned':
            excluded += 1
            continue
        occurred = row.get('occurred_at')
        if not isinstance(occurred, (int, float)) or kst(occurred) > as_of:
            continue
        if row.get('status') not in ('confirmed_synthetic', 'confirmed_user'):
            continue
        observed.append(row)
    return observed, excluded


def anchor_for(u: UserData, account_id: str, as_of: datetime) -> tuple[datetime, int, str, list[str]]:
    warnings = []
    source_field = 'account_balance_eod' if account_id == u.profile['payment_account_id'] else 'reserve_balance_eod'
    candidates = [r for r in u.daily if r.get('observation_complete') and not r.get('future_scenario') and r.get(source_field) is not None and eod(date.fromisoformat(r['date'])) <= as_of]
    if candidates:
        latest = max(candidates, key=lambda r: r['date'])
        return eod(date.fromisoformat(latest['date'])), int(latest[source_field]), 'observed_synthetic_daily_eod', warnings
    checks = [r for r in u.balance_checks if r.get('account_id') == account_id and kst(r['at']) <= as_of]
    if checks:
        latest = max(checks, key=lambda r: r['at'])
        warnings.append('no_observed_daily_eod_anchor_using_manual_balance_check')
        return kst(latest['at']), int(latest['confirmed_balance']), 'manual_balance_check', warnings
    account = next((a for a in u.accounts if a['account_id'] == account_id), None)
    if account is None:
        raise FinanceProblem(f'unknown account_id for user: {account_id}')
    start = date.fromisoformat(u.daily[0]['date']) if u.daily else as_of.date()
    warnings.append('opening_balance_only_no_verified_snapshot')
    return datetime.combine(start, time.min, KST) - timedelta(milliseconds=1), int(account['opening_balance']), 'synthetic_opening_balance', warnings


def _event(u: UserData, account: str, identity: str, due: datetime, amount: int, label: str,
           kind: str, method: str = 'transfer', status: str = 'scheduled', source: str = 'dataset_schedule',
           occurred: datetime | None = None, essential: bool = False, ids: list[str] | None = None) -> CashEvent:
    return CashEvent(id=identity, user_id=u.user_id, account_id=account, debit_at=due,
                     occurred_at=occurred, amount=amount, label=label, kind=kind, payment_method=method,
                     transaction_status=status, transaction_source=source, essential=essential,
                     source_transaction_ids=ids or [])


def _cash_effect(row: dict, account: str, u: UserData) -> int | None:
    """No impact on payment account for credit authorization/cash wallet spend."""
    kind = row.get('kind')
    method = row.get('method')
    if kind == 'spend':
        return -int(row['amount']) if row.get('account_id') == account and method != 'credit' else None
    if kind in ('card_payment', 'saving'):
        return -int(row['amount']) if row.get('account_id') == account else None
    if kind in ('income', 'refund'):
        return int(row['amount']) if row.get('account_id') == account else None
    if kind == 'transfer_self':
        if row.get('account_id') == account:
            return int(row['amount']) * (1 if row.get('direction') == 'in' else -1)
        if row.get('source_account_id') == account:
            return -int(row['amount'])
    if kind == 'atm_withdrawal' and row.get('account_id') == account:
        return -int(row['amount'])
    return None


def recorded_events(u: UserData, account: str, observed: list[dict], anchor: datetime, end: datetime, warnings: list[str]) -> list[CashEvent]:
    events = []
    for t in observed:
        effect = _cash_effect(t, account, u)
        if effect is None:
            continue
        debit_ms = t.get('debit_at') if isinstance(t.get('debit_at'), (int, float)) else t.get('occurred_at')
        if debit_ms is None:
            warnings.append(f'missing_debit_at:{t["id"]}')
            continue
        due = kst(debit_ms)
        if not (anchor < due <= end):
            continue
        events.append(_event(u, account, 'txn:' + t['id'], due, effect, t.get('name', t.get('kind', '거래')),
                             t.get('kind', 'unknown'), t.get('method', 'transfer'), 'actual',
                             'user_confirmed_sms' if t.get('status') == 'confirmed_user' else 'dataset_observed_transaction', kst(t['occurred_at']), bool(t.get('essential')), [t['id']]))
    return events


def credit_bills(u: UserData, account: str, observed: list[dict], anchor: datetime, end: datetime,
                 warnings: list[str]) -> list[CashEvent]:
    if account != u.profile['payment_account_id']:
        return []
    bills = defaultdict(lambda: {'amount': 0, 'sources': [], 'due': None})
    confirmed_payments = {(t.get('card_id'), kst(t.get('debit_at') or t['occurred_at']).date())
                          for t in observed if t.get('kind') == 'card_payment'}
    for t in observed:
        if t.get('method') != 'credit' or t.get('kind') != 'spend':
            continue
        debit_ms = t.get('debit_at')
        if not isinstance(debit_ms, (int, float)):
            warnings.append(f'credit_missing_settlement_date:{t["id"]}')
            continue
        first_due = kst(debit_ms)
        installments = t.get('installments') or 1
        if not isinstance(installments, int) or installments < 1:
            warnings.append(f'invalid_installments:{t["id"]}')
            continue
        if installments > 1:
            warnings.append(f'installment_schedule_derived_from_count_no_issuer_statement:{t["id"]}')
        amount = t.get('amount')
        if type(amount) is not int or amount < 0:
            warnings.append(f'invalid_credit_amount:{t["id"]}')
            continue
        first_part, remainder = divmod(amount, installments)
        for i in range(installments):
            # No new purchase is created: these are settlement legs of an observed purchase.
            part = first_part + (remainder if i == installments - 1 else 0)
            date_due = next_month(first_due.date(), i)
            due = datetime.combine(date_due, first_due.timetz().replace(tzinfo=None), KST)
            if not (anchor < due <= end) or (t.get('card_id'), date_due) in confirmed_payments:
                continue
            key = (t.get('card_id', 'unknown'), date_due)
            bills[key]['amount'] += part
            bills[key]['sources'].append(t['id'])
            bills[key]['due'] = due
    return [_event(u, account, f'credit_bill:{card}:{day}', bill['due'], -bill['amount'], '관측된 승인 기반 신용카드 청구 추정',
                   'credit_card_bill', 'credit', 'scheduled', 'derived_from_observed_card_approvals',
                   essential=True, ids=sorted(bill['sources']))
            for (card, day), bill in sorted(bills.items()) if bill['amount']]


def scheduled_events(u: UserData, account: str, observed: list[dict], anchor: datetime, end: datetime,
                     include_uncertain: bool, income_delay_days: int, existing_bills: list[CashEvent]) -> list[CashEvent]:
    events = []
    paid_schedule_refs = {(t.get('schedule_id'), kst(t.get('debit_at') or t['occurred_at']).date())
                          for t in observed if t.get('schedule_id')}
    billed_dates = {e.debit_at.date() for e in existing_bills}
    start = anchor.date()
    for schedule in u.schedules:
        if schedule.get('account_id') != account:
            continue
        direction = schedule['direction']
        uncertain = direction == 'in' and schedule.get('status') == 'uncertain'
        if uncertain and not include_uncertain:
            continue
        occurrences = []
        if schedule.get('recurrence') == 'once' and schedule.get('date'):
            occurrences = [date.fromisoformat(schedule['date'])]
        elif schedule.get('recurrence') == 'monthly' and schedule.get('day'):
            m = start.year * 12 + start.month - 1
            last = end.date().year * 12 + end.date().month - 1
            for j in range(m, last + 1):
                y, zero = divmod(j, 12)
                occurrences.append(month_date(y, zero + 1, int(schedule['day'])))
        for scheduled_day in occurrences:
            if schedule.get('kind') in ('credit_card_bill', 'card_payment') and scheduled_day in billed_dates:
                continue
            if (schedule['id'], scheduled_day) in paid_schedule_refs:
                continue
            debit_day = scheduled_day + timedelta(days=income_delay_days if direction == 'in' else 0)
            due = midday(debit_day)
            if not (anchor < due <= end):
                continue
            signed = int(schedule['amount']) * (1 if direction == 'in' else -1)
            events.append(_event(u, account, f'schedule:{schedule["id"]}:{scheduled_day}', due,
                                 signed, schedule['name'], schedule.get('kind', 'fixed_expense'),
                                 schedule.get('method', 'transfer'), 'scheduled',
                                 'dataset_uncertain_schedule' if uncertain else 'dataset_registered_schedule',
                                 essential=bool(schedule.get('essential', False))))
    return events


def simulate_days(as_of: datetime, anchor_balance: int, events: list[CashEvent], horizon: int,
                  minimum: int = 0) -> tuple[list[DailyCashflow], list[dict]]:
    """Testable projection. Conservative daily low: all debits before unknown-time credits."""
    grouped = defaultdict(list)
    for e in events:
        if e.debit_at.tzinfo is None:
            raise FinanceProblem(f'naive event timestamp: {e.id}')
        if e.debit_at.astimezone(KST) <= as_of:
            continue
        day = e.debit_at.astimezone(KST).date()
        if day <= as_of.date() + timedelta(days=horizon):
            grouped[day].append(e)
    balance = anchor_balance
    days = []
    payment_results = []
    for offset in range(1, horizon + 1):
        day = as_of.date() + timedelta(days=offset)
        lines = sorted(grouped[day], key=lambda x: (x.debit_at, x.id))
        outflow = sum(e.amount for e in lines if e.amount < 0)
        inflow = sum(e.amount for e in lines if e.amount > 0)
        low = balance + outflow
        close = low + inflow
        # Essential commitments are conservatively charged before unverified same-day incoming cash.
        running = balance
        for e in sorted((e for e in lines if e.amount < 0), key=lambda x: (not x.essential, x.debit_at, x.id)):
            running += e.amount
            if e.essential:
                payment_results.append({'id': e.id, 'date': day.isoformat(), 'name': e.label,
                                        'amount': -e.amount, 'balance_after': running,
                                        'covered': running >= minimum})
        days.append(DailyCashflow(date=day, opening_balance=balance, inflow=inflow,
                                  outflow=-outflow, lowest_balance=low, closing_balance=close, events=lines))
        balance = close
    return days, payment_results


def _virtual(u: UserData, as_of: datetime) -> dict:
    snaps = [s for s in u.portfolio_snapshots if date.fromisoformat(s['date']) <= as_of.date()]
    if not snaps:
        return {'virtual_cash': None, 'positions': {}, 'actual_payment_account_impact': 0, 'status': 'no_snapshot'}
    snap = max(snaps, key=lambda s: s['date'])
    return {'snapshot_date': snap['date'], 'virtual_cash': snap['virtual_cash'],
            'positions': snap.get('positions', {}), 'actual_payment_account_impact': 0,
            'status': 'simulated_only'}


def _parse_as_of(as_of: date | datetime | None, ds: Dataset, allow_post_snapshot: bool = False) -> datetime:
    cutoff = date.fromisoformat(ds.meta['snapshot_date'])
    if as_of is None:
        return eod(cutoff)
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            raise FinanceProblem('as_of_datetime_must_include_timezone')
        parsed = as_of.astimezone(KST)
    else:
        parsed = eod(as_of)
    if parsed > eod(cutoff) and not allow_post_snapshot:
        raise FinanceProblem(f'as_of_exceeds_observation_cutoff:{cutoff}')
    if parsed > eod(datetime.now(KST).date()):
        raise FinanceProblem('as_of_cannot_be_in_future')
    return parsed


def _selected(ds: Dataset, req: CalculationRequest, allow_post_snapshot: bool = False) -> tuple[UserData, str, datetime]:
    u = ds.get_user(req.user_id)
    account = req.account_id or u.profile.get('payment_account_id')
    if account not in {a['account_id'] for a in u.accounts}:
        raise FinanceProblem('account_does_not_belong_to_user')
    return u, account, _parse_as_of(req.as_of, ds, allow_post_snapshot=allow_post_snapshot)


def calculate(ds: Dataset, req: CalculationRequest, observed_overlay: list[dict] | None = None,
              allow_post_snapshot: bool = False) -> FinancialResult:
    u, account, as_of = _selected(ds, req, allow_post_snapshot=allow_post_snapshot)
    anchor, balance, source, warnings = anchor_for(u, account, as_of)
    observed, excluded = normalise_observed(u.transactions + tuple(observed_overlay or []), as_of)
    if observed_overlay:
        warnings.append('confirmed_sms_overlay_applied_after_dataset_snapshot_only')
    warnings.extend(['dataset_is_synthetic_not_real_bank_data', 'future_scenario_transactions_excluded'])
    warnings.append('no_ml_predictions_provided' if not req.forecast_events else 'caller_provided_ml_forecast_unverified')
    if req.minimum_required_balance is None:
        warnings.append('minimum_required_balance_missing_zero_overdraft_threshold_only')
    if not u.profile.get('savings_goal') and not ds.raw.get('savings_goals'):
        warnings.append('savings_goals_not_present_in_dataset')
    if req.include_uncertain_income:
        warnings.append('uncertain_income_included_by_explicit_request')
    if account != u.profile['payment_account_id']:
        warnings.append('reserve_account_only_explicit_transfers_are_projected')
    if source == 'observed_synthetic_daily_eod':
        checks = [r for r in u.balance_checks if r.get('account_id') == account and kst(r['at']) <= anchor]
        if checks:
            latest = max(checks, key=lambda r: r['at'])
            if latest['at'] < int(anchor.timestamp() * 1000):
                warnings.append(f'latest_manual_balance_check:{kst(latest["at"]).date().isoformat()}_newer_daily_snapshot_is_synthetic_reconciliation')

    # If as_of is midday, first replay only observed bank debits since previous snapshot,
    # without including future rows or approvals whose card bill has not been debited.
    actual_after_anchor = recorded_events(u, account, observed, anchor, as_of, warnings)
    balance += sum(e.amount for e in actual_after_anchor)
    horizon = max(14, req.horizon_days)
    end = eod(as_of.date() + timedelta(days=horizon))
    direct = recorded_events(u, account, observed, as_of, end, warnings)
    credit = credit_bills(u, account, observed, as_of, end, warnings)
    schedule = scheduled_events(u, account, observed, as_of, end, req.include_uncertain_income,
                                req.income_delay_days, credit)
    imported = []
    seen = {e.id for e in direct + credit + schedule}
    for group, desired in ((req.forecast_events, 'predicted'), (req.simulation_events, 'simulated')):
        for item in group:
            if item.user_id != u.user_id or item.account_id != account:
                raise FinanceProblem('injected_event_account_or_user_mismatch')
            if item.status != desired:
                raise FinanceProblem('event_status_mismatch')
            if item.id in seen:
                raise FinanceProblem(f'duplicate_event_id:{item.id}')
            seen.add(item.id)
            if item.debit_at.astimezone(KST) <= as_of:
                raise FinanceProblem('injected_event_must_be_after_as_of')
            imported.append(CashEvent(id=item.id, user_id=u.user_id, account_id=account,
                                      occurred_at=item.occurred_at, debit_at=item.debit_at.astimezone(KST),
                                      amount=item.amount, label=item.label, kind=item.kind,
                                      payment_method=item.payment_method,
                                      transaction_status=item.status, transaction_source=item.source,
                                      essential=item.essential))
    events = direct + credit + schedule + imported
    if not events and not observed:
        warnings.append('no_observed_financial_events')
    days, payments = simulate_days(as_of, balance, events, horizon, req.minimum_required_balance or 0)
    min_balance = min([balance] + [x.lowest_balance for x in days[:req.horizon_days]])
    future = {str(n): days[n-1].closing_balance for n in (3, 7, 14)}
    if req.horizon_days not in (3, 7, 14):
        future[str(req.horizon_days)] = days[req.horizon_days - 1].closing_balance
    choices = [r for r in u.purchase_intents if r.get('date', '') <= as_of.date().isoformat()]
    return FinancialResult(dataset_id=ds.meta.get('dataset_id', 'unknown'), user_id=u.user_id,
                           account_id=account, as_of=as_of, balance_snapshot_at=anchor,
                           balance_snapshot_source=source, available_cash=balance,
                           minimum_required_balance=req.minimum_required_balance,
                           future_balances=future, daily_cashflow=days[:req.horizon_days],
                           minimum_balance=min_balance,
                           minimum_cash_cushion=min_balance - req.minimum_required_balance if req.minimum_required_balance is not None else None,
                           essential_payments_covered=all(p['covered'] for p in payments if p['date'] <= days[req.horizon_days-1].date.isoformat()),
                           essential_payment_results=[p for p in payments if p['date'] <= days[req.horizon_days-1].date.isoformat()],
                           warnings=sorted(set(warnings)), virtual_investments=_virtual(u, as_of),
                           savings_goals=[], planned_purchases=choices[-5:],
                           observed_transaction_count=len(observed), excluded_oracle_transaction_count=excluded,
                           forecast_applied=bool(req.forecast_events))


def state_for(ds: Dataset, req: CalculationRequest, observed_overlay: list[dict] | None = None,
              allow_post_snapshot: bool = False) -> UserState:
    r = calculate(ds, req, observed_overlay=observed_overlay, allow_post_snapshot=allow_post_snapshot)
    u = ds.get_user(req.user_id)
    as_of_day = r.as_of.date()
    incomes = [s for s in u.schedules if s.get('direction') == 'in' and s.get('account_id') == r.account_id]
    expenses = [s for s in u.schedules if s.get('direction') == 'out' and s.get('account_id') == r.account_id]
    card_bills = [e.model_dump(mode='json') for d in r.daily_cashflow for e in d.events if e.kind == 'credit_card_bill']
    variable = sum(1 for t in u.transactions if not t.get('future_scenario') and t.get('kind') == 'spend' and not t.get('essential') and
                   kst(t['occurred_at']) <= r.as_of)
    choices = [i for i in u.purchase_intents if i.get('date', '') <= as_of_day.isoformat()]
    return UserState(dataset_id=r.dataset_id, user_id=r.user_id, account_id=r.account_id,
                     balance_snapshot_at=r.balance_snapshot_at,
                     balance_snapshot_source=r.balance_snapshot_source, available_cash=r.available_cash,
                     observation_start=date.fromisoformat(ds.meta['start_date']), observation_end=date.fromisoformat(ds.meta['snapshot_date']),
                     accounts=[dict(a) for a in u.accounts], scheduled_income=[dict(s) for s in incomes],
                     fixed_expenses=[dict(s) for s in expenses], credit_card_bills=card_bills,
                     variable_expense_count=variable, savings_goals=[],
                     virtual_investments=r.virtual_investments,
                     purchase_candidates=[dict(i) for i in choices[-5:]],
                     minimum_required_balance=req.minimum_required_balance, warnings=r.warnings)