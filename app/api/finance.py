from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from app.schemas import CalculationRequest, FinancialResult, UserState
from app.services.dataset import Dataset, DataProblem, load_dataset
from app.services.finance import calculate, state_for, FinanceProblem

router = APIRouter(prefix='/api/v4', tags=['v4-finance'])


def dataset_dep() -> Dataset:
    try:
        return load_dataset()
    except DataProblem as error:
        raise HTTPException(status_code=503, detail={'code': 'dataset_unavailable', 'message': str(error)}) from error


def handle(ds: Dataset, req: CalculationRequest, state: bool = False):
    try:
        return state_for(ds, req) if state else calculate(ds, req)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={'code': 'user_not_found', 'message': str(error)}) from error
    except FinanceProblem as error:
        raise HTTPException(status_code=422, detail={'code': 'invalid_financial_request', 'message': str(error)}) from error


@router.get('/health', summary='API 및 금융 데이터셋 상태 확인', description='원본 합성 데이터 로딩 여부와 기준일, 사용자 수를 확인합니다.')
def health(ds: Dataset = Depends(dataset_dep)):
    return {'status': 'ok', 'dataset_id': ds.meta['dataset_id'],
            'users': len(ds.profiles), 'snapshot_date': ds.meta['snapshot_date'], 'synthetic': True}


@router.get('/users', summary='합성 사용자 20명 목록', description='실제 사용자 정보가 아닌 테스트용 S01~S20 ID를 반환합니다.')
def users(ds: Dataset = Depends(dataset_dep)):
    # Demo-only endpoint; never expose real users without authentication.
    return {'users': sorted(ds.profiles), 'synthetic': True, 'observation_end': ds.meta['snapshot_date']}


@router.get('/users/{user_id}/state', response_model=UserState, summary='결제계좌 현재 상태', description='관측 종료일 기준 결제계좌 잔액, 고정 수입·지출과 카드 청구 예정 정보를 계산합니다.')
def get_state(user_id: str, as_of: date | None = Query(default=None),
              account_id: str | None = None, ds: Dataset = Depends(dataset_dep)):
    return handle(ds, CalculationRequest(user_id=user_id, account_id=account_id, as_of=as_of), state=True)


@router.get('/users/{user_id}/cashflow', response_model=FinancialResult, summary='미래 현금흐름 계산 (3·7·14일)', description='등록된 일정만 사용해 기준일 이후의 계좌 잔액을 추정합니다. ML 예측은 자동 생성하지 않습니다.')
def get_cashflow(user_id: str, as_of: date | None = Query(default=None),
                 account_id: str | None = None, horizon_days: int = Query(default=14, ge=1, le=90),
                 minimum_required_balance: int | None = Query(default=None, ge=0),
                 include_uncertain_income: bool = False, income_delay_days: int = Query(default=0, ge=0, le=31),
                 ds: Dataset = Depends(dataset_dep)):
    return handle(ds, CalculationRequest(user_id=user_id, account_id=account_id, as_of=as_of,
                                         horizon_days=horizon_days, minimum_required_balance=minimum_required_balance,
                                         include_uncertain_income=include_uncertain_income,
                                         income_delay_days=income_delay_days))


@router.post('/cashflow/simulate', response_model=FinancialResult, summary='가상 구매·소득 변동 시나리오 비교', description='simulation_events 또는 외부 forecast_events를 적용한 결과를 산출합니다. 원본 JSON 데이터는 변경하지 않습니다.')
def simulate(payload: CalculationRequest, ds: Dataset = Depends(dataset_dep)):
    return handle(ds, payload)

@router.get('/users/{user_id}/dataset-snapshot', summary='기준일 이전에 관측된 사용자별 금융 원본',
            description='읽기 전용 합성 데이터. as_of를 지정하면 그 날짜 이후에 발생한 실제 거래·투자·잔액 확인 내역을 공개하지 않습니다.')
def dataset_snapshot(user_id: str, as_of: date | None = Query(default=None),
                     ds: Dataset = Depends(dataset_dep)):
    """Past-date inspections cannot inspect future observed records or oracle scenarios."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    try:
        u = ds.get_user(user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={'code': 'user_not_found'}) from error
    snapshot = date.fromisoformat(ds.meta['snapshot_date'])
    cutoff = as_of or snapshot
    if not date.fromisoformat(ds.meta['start_date']) <= cutoff <= snapshot:
        raise HTTPException(status_code=422, detail={'code': 'observation_date_out_of_range'})
    after_cutoff_ms = int(datetime.combine(
        cutoff + timedelta(days=1), datetime.min.time(),
        tzinfo=ZoneInfo('Asia/Seoul')).timestamp() * 1000)

    def known(row):
        if row.get('future_scenario') or row.get('synthetic_oracle_only') or row.get('phase') == 'future_scenario':
            return False
        stamp = row.get('at', row.get('occurred_at'))
        if isinstance(stamp, (int, float)):
            return stamp < after_cutoff_ms
        return row.get('date', '9999') <= cutoff.isoformat()

    observed = [t for t in u.transactions if known(t)
                and t.get('status') in ('confirmed_synthetic', 'confirmed_user')]
    daily = [d for d in u.daily if d.get('date', '') <= cutoff.isoformat()
             and d.get('observation_complete') and not d.get('future_scenario')]
    return {
        'dataset_id': ds.meta['dataset_id'], 'user_id': user_id,
        'snapshot_date': cutoff, 'synthetic': True,
        'profile': u.profile, 'accounts': list(u.accounts),
        'transactions': observed, 'schedules': list(u.schedules),
        'daily': daily,
        'balance_checks': [b for b in u.balance_checks if known(b)],
        'pending_card_liabilities': [b for b in u.pending_card_liabilities
                                     if not b.get('future_scenario') and b.get('due_date', '9999') >= cutoff.isoformat()],
        'investments': [i for i in u.investments if known(i)],
        'purchase_intents': [p for p in u.purchase_intents if known(p)],
    }
