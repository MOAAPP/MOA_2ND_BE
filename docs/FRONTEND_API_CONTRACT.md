# 백엔드 인수인계: 평행세계·적금·다종목 투자·목표 Agent

기준 브랜치: `feat/world-agent`. 프론트 `MOA_2ND` 코드를 수정하지 않고 독립적으로 구현한 FastAPI 계약입니다.

## 현재 프론트와 연동 경계

| 기존 프론트 파일 (`MOA_2ND/main`) | 읽는 백엔드 데이터 | 현 상태 |
| --- | --- | --- |
| `src/lib/stage3Api.ts` | `POST /api/v4/scenarios/compare` | 기존 6경로 응답 유지 |
| `src/lib/v4AiApi.ts` | `POST /api/v4/agent/analyze` | LLM 기반 기존 분석 유지 |
| `src/components/Stage3Finance.tsx` | `paths[].daily_cashflow`, `future_balances` | 기존 화면 수정 없음 |
| `src/components/Stage3Ai.tsx` | 기존 에이전트의 `search.routes` | 기존 화면 수정 없음 |
| `src/screens/bank/SaveHome.tsx` | 프론트 자체 가상 저축/계약 엔진 | FastAPI API와 **아직 연결되지 않음** |
| `src/screens/Assets.tsx` | 프론트 자체 저축·투자 화면 | FastAPI API와 **아직 연결되지 않음** |

프론트 기본 홈과 `?demo=1` 테스트용 FastAPI 화면은 별개입니다. 기존 API를 교체하지 않고 새로운 API를 추가한 이유입니다. 실제 화면의 새 API 호출은 프론트 담당자가 연결하는 단계에 남아 있습니다.

## 새 통합 목표 Agent

`POST /api/v4/agent/goal-plan`: **한 번의 요청**으로 구매·저축·다종목 매매 조건을 교차 탐색합니다. LLM 없이 사용자가 확정한 금액·날짜만 사용합니다. `/agent/analyze`의 자연어 조건 추출 기능과는 아직 **자동 연결되지 않습니다**.

```json
{
  "user_id": "S06",
  "as_of": "2026-11-04",
  "horizon_days": 14,
  "savings_plan": {
    "user_id": "S06",
    "as_of": "2026-11-04",
    "goal_amount": 40000,
    "current_savings": 10000,
    "deposit_amount": 5000,
    "duration_months": 6,
    "forecast_daily_spend_override": 0
  },
  "purchase_amount": 1000,
  "purchase_required": false,
  "purchase_delay_days": 3,
  "investment_required": false,
  "investment_trades": [
    {"trade_id":"etf-buy","code":"ETF-A","asset_type":"etf","side":"buy","trade_date":"2026-11-05","quantity":2,"unit_price":1000},
    {"trade_id":"stock-buy","code":"STOCK-B","asset_type":"stock","side":"buy","trade_date":"2026-11-06","quantity":1,"unit_price":500},
    {"trade_id":"etf-sell","code":"ETF-A","asset_type":"etf","side":"sell","trade_date":"2026-11-09","settlement_date":"2026-11-10","quantity":1,"unit_price":1200}
  ],
  "investment_price_marks": [
    {"code":"ETF-A","date":"2026-11-11","unit_price":900},
    {"code":"STOCK-B","date":"2026-11-11","unit_price":400}
  ]
}
```

### 응답/그래프 주요 필드

- `status`: `ok`, `no_valid_route`, `base_cashflow_at_risk`.
- `recommended_route_id`: **검증에 통과한 경로**에만 부여. 실패 시 `null`.
- `routes[]`: 최대 `max_results`개. `purchase_strategy` / `savings_strategy` / `investment_included`로 각 가상 세계 조합을 구분.
- `routes[].checks`: `minimum_balance`, `essential_payments`, `required_purchase`, `required_investment`, `required_savings_goal`, `savings_deposit_validated`의 실제 통과 여부.
- `routes[].reasoning[]`: 추천 조건 설명, 예상 저축 목표 달성일 또는 부족액, 검증 기간의 한계.
- `routes[].chart[]`: 날짜별 `payment_account_balance`, `balance_delta_vs_no_actions`, `savings_principal_balance`, `portfolio_market_value`, `portfolio_total_profit_loss`, `total_virtual_assets`. 프론트 차트 입력으로 사용 가능.
- `requested_plan_checks`: 사용자가 요청한 첫 조합의 검증 결과. 추천 불가한 경우에도 이유를 설명하는 데 사용.
- `baseline_minimum_payment_account_balance` 및 `baseline_essential_payments_covered`: 아무 가상 선택도 하지 않았을 때의 계좌 위험. 이 기준부터 위험하면 새 적금·투자를 안전하다고 추천하지 않음.
- `assumptions[]`, `conflict_explanations[]`: 합성 데이터, 추정 한계, 불가능한 조합 설명.

**현금흐름 계산:** 동일한 기준일·ML 또는 사용자 지출 가정으로 기준 경로를 계산하고, 각 대안의 구매·적금·주식·ETF 매수대금 및 매도 정산 입금을 **모두 합친 뒤 기존 Python 금융 엔진에 전달**합니다. 각 대안을 따로 계산해 나중에 잔액만 더하는 방식이 아닙니다. `balance_delta_vs_no_actions`에는 가상 선택으로 발생한 결제계좌 증감을 표시합니다.

**목표 판단:** `projected_savings_at_maturity`는 사용자가 설정한 적금 납입액과 이자 가정의 산술적 만기 예측입니다. 투자 평가액은 적금 목표 달성 금액에 더하지 않습니다. 최대 `horizon_days`(90일)만 계좌 현금흐름을 검증하고 미래 장기 현금흐름을 보장하지 않습니다. `savings_goal_required_by_maturity=true`는 이 목표 미달 조합을 추천에서 제외합니다.

**투자:** 종목 최대 12개, 매매 최대 60개. 해당 요청에서 가상 매수한 수량만 매도 가능. 종목별 사용자 입력 단가/평가단가, 이동평균 원가, 정산일만 사용. 실제 시세 예측·거래 실행·수수료/세금은 제공하지 않습니다. `valuation_loss_percent`는 평가자산에만 적용하며 결제계좌에서 같은 손실을 다시 빼지 않습니다.

## 기존 API — 유지

| 경로 | 목적 |
| --- | --- |
| `POST /api/v4/scenarios/compare` | 기존 6가지 독립 평행세계 시뮬레이션 |
| `POST /api/v4/savings/plan` | 적금 납입 계획 및 최대 90일 현금흐름 검증 |
| `POST /api/v4/agent/savings/recommend` | 적금 납입액·연기 대안 추천 |
| `POST /api/v4/portfolio/simulate` | 가상 다종목 매매·일별 투자 평가액 |
| `POST /api/v4/agent/search` | 구매 중심 기존 제약조건 경로 재탐색 |
| `POST /api/v4/agent/analyze` | 기존 LLM 자연어 조건 추출 및 Tool Calling |

`/agent/goal-plan`은 별도 opt-in API이며 기존 프론트에서 자동 호출되지 않습니다.

## 로컬 실행 및 확인

```powershell
git switch feat/world-agent
git pull origin feat/world-agent
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Swagger UI: `http://127.0.0.1:8000/docs`

프론트 개발 프록시는 `/api/v4 -> http://localhost:8000`이며 배포 환경에서는 `VITE_FINANCE_API_BASE`로 FastAPI 서버를 가리키도록 설정합니다.

### 최종 검수 기준

1. `pytest -q` 실행 시 신규 및 기존 테스트가 모두 통과하는지 확인.
2. 같은 입력의 6경로 API 구조가 변경되지 않았는지 확인.
3. 구매·저축·ETF 매수·매도 정산 현금흐름이 **한 번씩만** 반영되고, 만기 예상/투자 평가액을 결제계좌 현금으로 오인하지 않는지 확인.
4. 최소잔액·필수결제 조건을 충족하지 못한 경로가 `recommended_route_id`로 나오지 않는지 확인.
5. 프론트 담당자의 화면 개발 완료 후 FastAPI 시연 화면뿐 아니라 일반 앱에서 새 엔드포인트 연동 테스트 수행.

> 현재 합성 사용자 데모 API는 금융 데이터 인증이 없는 실서비스 환경에서 사용하면 안 됩니다.


## 자연어 Agent / 경로 탐색: 추천 불가 사유 안내

`POST /api/v4/agent/analyze`은 기존 `search` 객체에, `POST /api/v4/agent/search`는 응답 최상위에 **`no_route_guidance`**를 추가로 반환합니다. 기존 `status`, `routes`, `rejection_counts` 계약은 변경하지 않습니다.

- `status="ok"` 또는 `"needs_confirmation"`일 때 `no_route_guidance=null`.
- `status="no_valid_route"`일 때 `no_route_guidance.reason`: `baseline_cashflow_at_risk`(아무것도 하지 않아도 마이너스 등), `minimum_balance_unmet`(사용자 최소잔액/필수결제 조건 미달), `scenario_constraints_unmet`(기본 현금흐름은 통과하나 요청한 조합 불가), `baseline_unavailable`(미구매 기준 계산 실패).
- `no_route_guidance.baseline_no_purchase`: **동일한 Python/ML 예측 조건에서 계산한 미구매 경로**의 `horizon_days`, `forecast_source`, `available_cash`, `minimum_balance`, `ending_balance`, `required_minimum_balance`, `first_shortfall_date`, `minimum_balance_met`, `essential_payments_covered`. 조회 불가 시 `null`.
- `no_route_guidance.summary`는 기존 `conflict_explanations[0]`에도 추가하므로 현재 프론트가 기존 오류 설명만 읽어도 근거를 확인할 수 있습니다.
- `no_route_guidance.next_steps`는 **사용자가 확인할 선택지**일 뿐 실제 가계부 변경이나 검증된 비용 절감 결과가 아닙니다. `actions_are_validated=false` 및 `disclaimer`를 함께 표시하세요.
- 프론트, ML, 공통 금융 계산 엔진은 변경하지 않았으며 실제 결제, 투자, 자동이체는 발생하지 않습니다.


## 자연어 Agent: 실제 경로를 위한 검증 기간 선택

`POST /api/v4/agent/analyze`는 선택적 `horizon_days` (14~90일, 기본 30일)를 지원합니다. 모델의 금융 도구와 Python 경로 검증 모두 이 기간을 사용합니다.

- S06 / `2026-11-04` / 구매 희망 1000원, 필수 구매 아님 / `horizon_days=14`: 양성 경로 사례 `status="ok"` 및 `search.routes`가 반환되는지 검증
- 같은 조건에 기간 생략(기본 30일): `status="no_valid_route"`, `search.no_route_guidance` 유지
- 자동화는 **LLM 응답만 모의(mock)로 제공**하고 실제 합성 데이터와 Python 계산은 사용합니다. 실제 학교 FactChat 호출은 로컬 키로 별도 Swagger 확인 필요
- 14일 경로의 유효성은 30일 안전을 의미하지 않으며 기간 선택 조건을 화면에 명시해야 합니다.
