# 평행통장 V4.0 — 5단계 구현 및 검증 가이드

## 저장소/의존성
- React PWA: `MOAAPP/MOA_2ND` PR #38 (`fix/v4-stage5-demo-agent-ui`)
- Python FastAPI: `MOAAPP/MOA_2ND_BE` PR #4 (`feat/v4-stage5-agent-paths`)
- 백엔드 5단계는 **아직 병합되지 않은 4단계 브랜치** `feat/v4-stage4-lightgbm-cashflow` 위에서 개발됨. `main`만 체크아웃하면 새 API가 없어 프론트가 비게 됨.
- 두 PR은 의도적으로 **Draft**, 기본 브랜치 자동 덮어쓰기 없음.

## 실제 데이터 연결
- 원본: `app/data/source/moa_20users_financial_test_JSON.zip`.
- 기준/범위: `S01` 기본 시연, 20명 `S01`~`S20`; 2026-09-11~2026-11-04 관측. 11/05 이후 자료는 미래 가정, 관측으로 사용 금지.
- `app/services/dataset.py`는 실행 디렉터리와 독립적인 `app/core/settings.py`의 프로젝트 루트 기준 경로를 사용한다. `MOA_DATASET_PATH`로 명시적으로 덮어쓸 수 있다. 누락/파싱 오류는 503으로 반환.
- API 기본 사용자 ID는 `MOA_DEFAULT_DEMO_USER=S01`로 덮어쓸 수 있음. 원본 계좌 정보 키는 `account_id`, 프로필 기본 결제계좌는 `payment_account_id`.
- 데이터셋에는 **사용자별 독립 savings_goals 컬렉션이 없음**. 원본 값으로 가장하지 않고 목표 금액을 사용자에게 확인함.
- 프론트는 React `Stage3Provider`의 `getDemoDefault` → `getDemoUsers` → `getDemoHome`로 실제 홈 상태를 불러오고 사용자/날짜 변경 시 전체 재요청하며 이전 데이터를 초기화함. 부가적인 가격 API 실패만으로 결제계좌 화면까지 차단되지 않도록 수정함.

## 실행 (터미널 1: Python 백엔드)
```sh
git clone https://github.com/MOAAPP/MOA_2ND_BE.git
cd MOA_2ND_BE
git fetch origin
git checkout feat/v4-stage5-agent-paths
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# 선택사항: Python agent를 사용하려면 실제 서버 환경에 아래 키 지정
# export MOA_AGENT_API_KEY="..."
# export MOA_AGENT_MODEL="gpt-4.1-mini"
# export MOA_AGENT_INPUT_USD_PER_M="실제_계약_단가"
# export MOA_AGENT_OUTPUT_USD_PER_M="실제_계약_단가"
python -m app.ml.train --output app/data/models
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## 실행 (터미널 2: React PWA)
```sh
git clone https://github.com/MOAAPP/MOA_2ND.git
cd MOA_2ND
git fetch origin
git checkout fix/v4-stage5-demo-agent-ui
npm ci
npm run dev:web
```
- 프론트: http://127.0.0.1:5173/?demo=1 (로컬 개발: `/`도 새 방문 시 시연 세션으로 진입)
- Swagger: http://127.0.0.1:8000/docs
- 임의의 실계좌 잔액은 만들지 않고, 백엔드가 꺼져 있으면 오류 UI 표시
- Vite 개발 프록시는 `/api/v4` → `localhost:8000`. 배포 시 `VITE_FINANCE_API_BASE`에 Python FastAPI의 HTTPS 공개 주소 설정 및 `MOA_CORS_ORIGINS` 일치 확인.

## 필수 Swagger API
- `GET /api/v4/health` (원본 ZIP 연결)
- `GET /api/v4/users` (20명 ID)
- `GET /api/v4/demo/default` (S01/기준일/관측범위)
- `GET /api/v4/users/S01/home?as_of=2026-11-04` (메인 화면 전체)
- `GET /api/v4/users/S02/home?as_of=2026-11-03` (날짜·사용자 분리)
- `GET /api/v4/users/S01/state?as_of=2026-11-04` (결제계좌 상태)
- `GET /api/v4/users/S01/transactions?as_of=2026-11-04` (과거 거래)
- `GET /api/v4/users/S01/schedules?as_of=2026-11-04` (필수결제·급여)
- `GET /api/v4/users/S01/cashflow?as_of=2026-11-04&horizon_days=14` (확정 금융 흐름)
- `GET /api/v4/users/S01/forecast?as_of=2026-11-04` (4단계 ML)
- `GET /api/v4/agent/dataset/audit` (외래키·전체 사용자 수)
- `POST /api/v4/agent/analyze` (실제 LLM Tool Calling + 파이썬 경로탐색)
- `POST /api/v4/agent/search` (LLM 불필요; 수정된 조건으로 탐색)
- `POST /api/v4/agent/routes/save`
- `GET /api/v4/agent/routes/{route_id}?user_id=S01`
- `POST /api/v4/agent/routes/{route_id}/recalculate?user_id=S01`

### 분석 요청 예시
```json
{
  "user_id": "S01",
  "as_of": "2026-11-04",
  "goal": "운동화는 이번 달 사고 싶지만 월세는 꼭 내야 하고, 여행 저축도 유지하고 싶어",
  "stress": "baseline",
  "max_evaluations": 72
}
```
- 서버 환경변수 `MOA_AGENT_API_KEY` 미설정 시 **503 `llm_unavailable`**; 기존 `/scenarios/compare`는 그대로 작동.
- 모델이 해석하지 못한 저축 목표 금액·모호한 구매 날짜 등은 `needs_confirmation`.
- 사용자가 `overrides`를 명시해 수정하면 모델 해석보다 우선함. 서버가 사용자 ID와 기준일을 고정한다.

### 키 없이 Python 탐색 검증
```json
{
  "conditions": {
    "user_id": "S01",
    "as_of": "2026-11-04",
    "purchase_required": false,
    "purchase_amount": 7000,
    "minimum_balance": 0,
    "allowed_payment_methods": ["debit"],
    "investment_allowed": false,
    "hard_constraints": ["월세·카드대금 보호"],
    "soft_preferences": ["가능하면 구매"]
  },
  "horizon_days": 30,
  "max_evaluations": 72,
  "max_results": 4,
  "stress": "baseline"
}
```
금액 단위 원(KRW); LLM은 금액 계산을 수행하지 않음. 반환되는 경로는 `hard_constraints_met`와 Python 체크를 통과한 경로뿐임.

## 평가/회귀 테스트
```sh
python -m pytest -q
python -m scripts.eval_stage5 --output /tmp/stage5-evaluation.json
```
- CI 액션 `validate-stage5.yml`: 원본 ZIP SHA 확인 → 기존 Stage3/4 회귀 → Stage5 제약·도구·오류 테스트 → 20명 고정6/최적화 비교 JSON 생성.
- React CI `validate-stage3-browser.yml`: 20명 전환·S01/S02 잔액·기준일 이동·React DOM 잔액과 API 값 일치·응답 스키마 오류·여섯 평행세계 + 신규 탐색 UI/LLM 없는 fallback.
- LLM 실호출의 정확도·Tool Calling 성공률·요금 측정은 실제 API 키/요금·토큰/샘플 시나리오 평가가 있어야 가능. **CI에서는 유료 실호출을 실행하지 않음.**

## 현재 한계
- category별 변동 소비 감축을 기존 엔진이 출처·카테고리별로 증명할 수 없어 경로 후보에서 거부함(가짜 환급 생성 금지).
- 가상투자 수익률은 시나리오 가정. 위험확률 계산이나 실제 시세 인증을 하지 않음.
- 데이터셋은 합성 사용자 데이터, 실사용자 20명의 계좌가 아님.
- 현 단계는 로컬 시연용. 배포 전에 인증·소유권·요청 제한·데이터 격리·제3자 LLM 정보전송 정책 검토 필요.
- 브랜치 의존성을 해결하고 실제 홈 브라우저 테스트가 통과하기 전에는 ‘배포 완료’가 아님.
