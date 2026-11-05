# V4.0 4단계 — 변동 결제계좌 현금유출 예측

## 데이터와 누수 방지
- 기준 ZIP: app/data/source/moa_20users_financial_test_JSON.zip (고정 원본, 별도 DB 없음)
- 관측된 55일(9/11~11/4)만 사용, 이후 37일은 oracle scenario이므로 학습·평가 불가.
- 관측 완전한 날짜의 결제계좌 즉시 출금, 비필수 소비만 라벨로 사용. 카드 승인 자체는 결제계좌 지출이 아님. 카드 청구는 기존 finance.py가 별도로 한 번 처리.
- incomplete 관측일은 0으로 대체하지 않고 라벨에서 제외. 명시적인 complete+0만 0원.
- 날짜 특징은 예측 대상일 달력만 사용. 모든 소비/급여/누락 특징은 origin까지.
- 월세/확정 급여/카드 청구액은 ML로 예측하지 않음.
- 평행세계 구매 후보는 ML 입력에 넣지 않고 별도 simulated event로 계산.
- train/validation/test는 타깃 날짜 기준으로 구분. validation 시작은 관측 끝 30일 전, test 시작은 끝 15일 전. 절대 랜덤 분할하지 않음.
- 한국 공휴일은 코드에 정확한 휴일 달력이 없어 이번 버전 특징량에서 **제외**. 요일, 주말은 포함.

## 실행
```bash
cd MOA_2ND_BE
python -m pip install -r requirements.txt
python -m app.ml.train
python -m uvicorn app.main:app --reload --port 8000
```

모델·버전 파일은 기본적으로 app/data/models/variable_spend.txt / metadata.json / evaluation.json에 기록. 데이터셋 ZIP은 수정하지 않음. API 요청은 학습을 호출하지 않음.

## Swagger API
- GET /api/v4/users/S01/forecast?as_of=2026-11-04
- GET /api/v4/users/S01/forecast-cashflow?as_of=2026-11-04&scenario=base
- scenario: base / spending_high / income_delay / combined
- GET /api/v4/users/S01/home?as_of=2026-11-04 — 기본 홈에 동일 예측 통합
- POST /api/v4/scenarios/compare — 6개 동일 기본 예측 경로 + 개별 선택 이벤트
- 기존 GET /api/v4/users, /api/v4/demo/default, /api/v4/users/{id}/transactions 등 유지.

```json
{"user_id":"S01","forecast_as_of":"2026-11-04","horizon_days":14,"model_type":"weekday","model_version":"baseline-weekday-v1","forecast_run_id":"...","data_quality_status":"complete","daily":[{"date":"2026-11-05","amount":1000,"method":"weekday"}],"forecasts":[{"horizon_days":3,"predicted_variable_outflow":3000}]}
```

위 예시의 금액은 API 응답 예시 형식을 설명하기 위한 임의 값이며 실제 데이터/성능 결과가 아님.

## 해석 및 제한
- LightGBM 모델은 오프라인에서 실제 학습/저장되지만 배포 여부는 validation 7일 MAE로 결정. 성능이 떨어지면 더 나은 baseline을 선택.
- 모델 파일이 없으면 명시적으로 weekday baseline 반환. 학습한 모델로 오인하지 않게 model_type 전달.
- LightGBM 14일 이후 예측값은 요일 기준선으로 대체하며, daily[].method='weekday_tail' 표기.
- 25% 증가 지출은 검증된 불확실성 구간이 아닌 **시나리오 가정**.
- 테스트의 결제일 잔액/자금 부족은 일정 고정 + 관측 변수 유출의 proxy 비교이며 실제 은행 잔고 실측 성능이 아님.
- 관측기간이 극히 짧고 전체가 합성 데이터임. 실제 사용자 일반화 성능을 주장하지 말 것.
