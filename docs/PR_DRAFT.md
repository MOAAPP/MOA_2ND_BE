# PR 초안: 금융 엔진 검증·저축/투자 현금흐름·변동지출 예측 고도화

## 변경 요약
1. **금융 엔진 버그 수정** (`app/services/finance.py`)
   - 카드 대금을 청구일보다 일찍/늦게 결제하면 결제분 + 추정 청구분이 이중 차감되던 문제 → 같은 카드 ±5일 결제는 청구를 대체.
   - 고정비·급여를 일정일보다 일찍 납부하면 일정일에 다시 차감되던 문제 → 같은 `schedule_id` ±7일 결제는 일정을 대체.
   - 검증: 20명 × 1,005일 일별 잔액 변화가 엔진 현금효과와 100% 일치, 월말(1/31→2/28, 윤년 2/29)·급여 지연 정상.
2. **저축·투자 현금흐름** (`app/services/stage3.py`, `app/stage3_models.py`)
   - 월 정기 저축만 입력해도 납입 생성(기존 0건), 최대 12회.
   - `savings_annual_rate` 입력 시 일 단순이자 반영(`virtual_savings_interest`), `investment_return_mode=linear` 추가(기본 `immediate`, 기존 동작 유지).
   - 경로별 `balance_delta_vs_no_purchase`, `choice_cash_outflow` 추가. 응답은 필드 추가만(비파괴). Swagger 예시 2개.
3. **변동지출 예측** (`app/ml/forecast.py`, `app/ml/train.py`)
   - LightGBM 다중 호라이즌 학습·정규화, 베이스라인 3종(전체 평균/최근 7일/요일별), MAE·RMSE·편향, 오류 분석표.
4. **그래프용 API** (`app/api/forecast_chart.py`, 신규)
   - `GET /api/v4/users/{id}/forecast-chart`, `GET /api/v4/ml/evaluation`; `GET /forecast`에 `model` 선택 파라미터와 `ForecastResponse` 응답 모델/예시 추가.
- 기준일(2026-11-04)·합성 데이터셋·기존 스키마 변경 없음. 새 API 키/환경변수 없음.

## 모델 성능 비교 (테스트 구간 10/21~11/4, 합성 데이터, 기간 합계 오차 단위 원)
| 모델 | 3일 MAE | 7일 MAE | 7일 RMSE | 14일 MAE | 7일 일 MAE |
|---|---|---|---|---|---|
| 전체 평균 | 30,488 | 46,192 | 77,053 | 93,564 | 12,850 |
| 최근 7일 이동평균 | 41,857 | 82,955 | 125,864 | 98,787 | 17,409 |
| 요일별 평균 | 28,889 | 45,814 | 76,626 | 92,613 | 12,714 |
| **LightGBM** | 29,817 | 53,271 | 90,954 | 124,587 | **11,775** |

- LightGBM은 **일 단위 오차 최소**, 기간 합계는 과소예측 편향(-38k)으로 평균 계열보다 나쁨. 원인: 지출 수준 상승(학습 0.951→테스트 1.080) 및 고지출일 과소예측. 전체 표·오류 분석: `docs/V5_FORECAST_EVAL.md`.
- 검증 7일 MAE 기준 서비스 기본 모델은 `overall`. 실사용자 성능을 의미하지 않음(합성 55일).

## 테스트 결과
- `pytest`: **151 passed** (기존 126 + 신규 25: 엔진 회귀 6, 저축/투자 6, 예측·그래프 API 13). 모델 폴더가 없는 CI 환경에서도 통과(테스트가 임시 폴더에 직접 학습).
- 실제 서버(uvicorn) 호출 확인: `forecast-chart`(예측/백테스트/학습 이전 기준일 422), `forecast?model=`, `ml/evaluation`, `forecast-cashflow`, `/docs` 200.
- CI 워크플로(`validate-stage4-ml.yml`)의 허용 모델 목록에 `overall` 추가.

## Swagger 테스트 방법
1. `pip install -r requirements.txt && python -m app.ml.train` (모델은 `app/data/models/`에 생성, gitignore 대상 — 배포 환경에서도 학습 필요)
2. `python -m uvicorn app.main:app --reload --port 8000` → http://localhost:8000/docs
3. `v4-ml-forecast` → **GET /api/v4/users/{user_id}/forecast-chart**: `user_id=S01`, `horizon_days=7` 실행 → `series[]`에 `date/predicted/actual/lightgbm/baseline_*`. 백테스트: `as_of=2026-10-25`(actual 채워짐, `live_metrics`로 모델별 오차 비교).
4. **GET /api/v4/ml/evaluation**: 성능 비교표·오류 분석.
5. **POST /api/v4/scenarios/compare**: Request body 예시(S18)로 저축·투자 경로의 `balance_delta_vs_no_purchase`, `virtual_savings_interest` 확인.
