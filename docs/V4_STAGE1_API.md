# 평행통장 V4.0 — 1단계 Python 금융 계산 엔진

## 구동

Python 3.10+ / Node 22+ 권장. **별도의 데이터베이스가 없습니다.** 사용자별 원본은 읽기 전용 ZIP 내부 JSON입니다.

```bash
cd MOA_2ND_BE
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest
```

API 문서는 `http://localhost:8000/docs`, OpenAPI는 `/openapi.json`입니다.

React Vite 개발 서버는 `/api/v4` 경로만 FastAPI `:8000`으로 프록시하고 기존 `/api`는 Node `:8787`로 유지합니다. 프로덕션에서 FastAPI를 별도로 배포한 뒤 `VITE_FINANCE_API_BASE=https://<your-fastapi-origin>`을 **프론트 빌드 시** 지정해야 합니다. 기존 Node/Discord/주식 API를 제거하거나 포트 8787을 변경하지 않습니다.

## 원본 데이터 파일

`app/data/source/moa_20users_financial_test_JSON.zip`은 업로드한 원본 ZIP의 이름만 안전하게 바꾼 복사본으로, 내부 파일은 수정하지 않았습니다. 현재 백엔드 저장소에 원본 ZIP을 별도로 보존합니다. 원본 파일이 없으면 API가 명시적 503을 반환합니다. 다른 샘플 숫자로 무단 대체하지 않습니다. 데이터 위치를 바꾸려면 `MOA_DATASET_PATH=/absolute/path/data.zip` 설정.

## API

| 경로 | 방법 | 의미 |
|---|---|---|
| `/api/v4/health` | GET | 실제 파일 로딩/검증 및 관측 컷오프 확인 |
| `/api/v4/users` | GET | 합성 사용자 ID 조회(테스트 전용) |
| `/api/v4/users/{user_id}/state` | GET | 사용자 계좌/스냅샷/고정 일정/가상 투자 분리 |
| `/api/v4/users/{user_id}/cashflow` | GET | 3/7/14일 잔액, 날짜별 이벤트, 필수결제·최저잔액 |
| `/api/v4/cashflow/simulate` | POST | 원본 불변 상태에서 외부 예측·가상 사건 주입 후 계산 |

조회 파라미터: `as_of=2026-11-04`, `horizon_days=14`, `account_id=S01-PAY`, `minimum_required_balance=30000`, `income_delay_days=3`, `include_uncertain_income=false`.

POST 예시: `{"user_id":"S01","minimum_required_balance":30000,"simulation_events":[{"id":"demo-buy","user_id":"S01","account_id":"S01-PAY","debit_at":"2026-11-06T12:00:00+09:00","amount":-89000,"kind":"purchase","label":"가상 운동화","status":"simulated","source":"simulation"}]}`

ML 연결: `forecast_events` 각 항목에는 `status="predicted"`, `source="model"`, `forecast_run_id`가 필수이며 이번 단계에서 API가 모델을 실행하거나 예측값을 생성하지 않습니다. 실제 계좌에 적립되는 금액과 가상 자산은 서로 더하지 않습니다.

## 데이터 처리 원칙

- 기본 `as_of`는 데이터셋 관측일 2026-11-04 한국시간 EOD. 사용자 기기 날짜로 자동 이동하지 않습니다. 이 날짜를 넘어서는 `as_of` 입력은 거부됩니다.
- 최근 관측 완료 daily EOD를 스냅샷으로 사용. 10/29 마지막 수기 잔액 확인 이후 daily EOD가 합성 대조값이라는 사실을 경고에 명시합니다. 스냅샷 이전 거래를 다시 차감하지 않습니다.
- 거래 `future_scenario=true`, `scenario_planned`, `synthetic_oracle_only=true`는 기본 원장에 들어가지 않습니다. 다만 과거 관측된 신용 승인 건의 미래 청구 `debit_at`은 출금 이벤트로 파생할 수 있습니다.
- 카드 사용일에는 현금이 빠지지 않습니다. 기존 실제 카드 대금 출금이 있으면 그 청구일에 승인 기반 중복 청구를 생성하지 않습니다.
- 할부 회차 금액과 공식 청구 명세가 없는 경우 승인원금/개월 수 균등 분할(원화 나머지는 마지막 회차)로 **추정**하고 경고합니다. 이 규칙은 실제 카드사 출금명세와 다를 수 있습니다.
- 현금 지갑 지출은 결제계좌 출금으로 처리하지 않습니다. 본인 계좌 간 이체는 소비·새 수입이 아니라 계좌별 순이동으로 표시합니다.
- 미확정 급여는 기본 예측에서 제외합니다. 급여 지연은 `income_delay_days`로 가정 처리합니다.
- 최저 잔액은 같은 날짜의 미확인 입금보다 출금이 먼저 일어난다는 보수적 가정으로 계산됩니다. 실거래 순서가 확인되면 정확한 `debit_at` 시각으로 개선해야 합니다.
- 원본 dataset의 `savings_goals` 필드는 없음. 임의 목표를 추가하지 않으며 응답에 빈 배열·경고를 제공합니다.
- 사용자별 설정/시뮬레이션은 메모리 요청별로 복제해 계산하므로 저장도 잠금도 필요하지 않습니다. 별도 JSON 저장이 도입되는 단계에서는 atomic rename + 파일락이 필요합니다.

## 기존 기능과 접점

- 기존 `src/lib/moa/ledger.ts`, `risk.ts`, `forecast.ts`, `src/lib/cashflow.ts`, `src/lib/worldWallet.ts`는 안정성 검증 없이 삭제하지 않았습니다. 기존 화면과 기능은 유지됩니다.
- `FinanceEnginePanel`은 합성 데모에서만 FastAPI 값을 표시합니다. 실제 사용자의 수동 등록 데이터는 아직 Python으로 동기화하지 않습니다. 기존 TypeScript 위험 카드와 신규 Python 계산 카드가 동시에 보이며 둘이 동일 결과라고 보장하지 않습니다.
- 후속 2단계에서 실제 거래 입력→백엔드로 변경 이벤트 전송, 날짜별 현금흐름 차트의 데이터원 Python 단일화, 구매 의도 멀티모달 입력을 `simulation_events`에 연결합니다.
- `/api/v4`가 외부에 공개되는 경우 현재는 인증 없는 합성 데이터 *시연 전용*입니다. 실데이터를 넣기 전에 사용자 인증·인가, 비밀값 관리, 전송 구간 암호화가 필수입니다.

## 현재 한계

1. 현재 단계는 ML 훈련/모델 예측과 LLM 에이전트를 구현하지 않습니다.
2. 원본 데이터에 취소·환불·ATM 인출 케이스가 없어 해당 엣지 케이스는 **별도 테스트 fixture**로 검증했습니다.
3. 실제 저축 목표는 데이터셋에 없으며, 가상 투자 파일은 실제 은행 잔액으로 합산하지 않습니다.
4. 11/4 기준 신용카드 미결제 합계는 과거 승인에서 유도했으며, 원장에 명시된 미래 가상 카드 대금 거래 자체를 확정으로 사용한 것은 아닙니다.
5. 개발 실행과 프로덕션 FastAPI 배포는 별개입니다. 환경변수·호스팅·CORS가 마련되어야 배포 PWA가 연결됩니다.

추가 데이터/저장소 검증 세부사항: [`V4_STAGE1_DATA_AUDIT.md`](V4_STAGE1_DATA_AUDIT.md).