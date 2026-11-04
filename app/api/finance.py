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


@router.get('/health')
def health(ds: Dataset = Depends(dataset_dep)):
    return {'status': 'ok', 'dataset_id': ds.meta['dataset_id'],
            'users': len(ds.profiles), 'snapshot_date': ds.meta['snapshot_date'], 'synthetic': True}


@router.get('/users')
def users(ds: Dataset = Depends(dataset_dep)):
    # Demo-only endpoint; never expose real users without authentication.
    return {'users': sorted(ds.profiles), 'synthetic': True, 'observation_end': ds.meta['snapshot_date']}


@router.get('/users/{user_id}/state', response_model=UserState)
def get_state(user_id: str, as_of: date | None = Query(default=None),
              account_id: str | None = None, ds: Dataset = Depends(dataset_dep)):
    return handle(ds, CalculationRequest(user_id=user_id, account_id=account_id, as_of=as_of), state=True)


@router.get('/users/{user_id}/cashflow', response_model=FinancialResult)
def get_cashflow(user_id: str, as_of: date | None = Query(default=None),
                 account_id: str | None = None, horizon_days: int = Query(default=14, ge=1, le=90),
                 minimum_required_balance: int | None = Query(default=None, ge=0),
                 include_uncertain_income: bool = False, income_delay_days: int = Query(default=0, ge=0, le=31),
                 ds: Dataset = Depends(dataset_dep)):
    return handle(ds, CalculationRequest(user_id=user_id, account_id=account_id, as_of=as_of,
                                         horizon_days=horizon_days, minimum_required_balance=minimum_required_balance,
                                         include_uncertain_income=include_uncertain_income,
                                         income_delay_days=income_delay_days))


@router.post('/cashflow/simulate', response_model=FinancialResult)
def simulate(payload: CalculationRequest, ds: Dataset = Depends(dataset_dep)):
    return handle(ds, payload)