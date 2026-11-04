# V4.0 1단계 — 원본 데이터셋 및 기존 프로젝트 분석

## 확인한 원본
- 최초 첨부: `moa_20users_financial_test_JSON (1).zip`; GitHub에는 내부 수정 없이 `backend/app/data/source/moa_20users_financial_test_JSON.zip`으로 복사
- 원본 ZIP SHA-256: `9e2adc5a7aa7720ef8daf24f8766389233dee399c0ea0826f9dc3a90aaeab609`
- 데이터셋 ID `MOA-20PERSONAS-92D-HIGH-FIDELITY-JSON-v3`, schema 3.1.0, seed 20261104
- `synthetic=true`, `contains_real_personal_financial_data=false`, KRW, Asia/Seoul
- 관측 기간: 2026-09-11~2026-11-04 (55일), 미래 가정: 2026-11-05~12-11 (37일)
- 미래 가정은 미래 거래의 정답이 아니며, 실제 또는 ML 예측으로 가장하지 않음

## 컬렉션 건수
| 컬렉션 | 원본 건수 |
|---|---:|
| profiles / accounts / cards | 20 / 40 / 40 |
| schedules | 154 |
| transactions | 12,777 |
| transactions: observed / future scenario | 7,595 / 5,182 |
| daily / balance_checks | 1,840 / 140 |
| pending_card_liabilities | 42 |
| purchase_intent_screenshots | 67 |
| investments / virtual_portfolio_snapshots | 220 / 460 |
| ingestion_events / choices | 560 / 440 |
| ml_feature_rows | 1,840 |
| cashflow_forecast_runs / ai_insights | 1,100 / 1,100 |

거래의 `kind` 분포: 소비 12,328, 본인 이체 229, 수입 161, 카드 대금 59. 여기에는 11월 5일 이후의 가정 거래도 섞여 있으므로 전체 12,777건을 실제 관측이라고 읽으면 안 됨. 원본의 AI 인사이트 및 ML 예측 자료 역시 실제 실행 모델 출력이 아니라 합성 기록임.

## 금융 경계/주의 사항
1. `profiles`는 20명이고, 각 계좌/스냅샷/거래는 `user_id`로 분리. 누락된 사용자나 고아 관계/중복 ID는 로더 단계에서 거부
2. 최근 관측 완료 `daily.account_balance_eod`가 결제계좌 기준 잔액. 과거 거래를 다시 빼지 않음. 최신 수기 잔액 확인 기록과 이 EOD 스냅샷은 다른 근거이며 구분 표기
3. 신용카드 구매는 승인일이 아닌 실제 결제계좌 출금일에 영향을 주며, 관측된 승인에 대응하는 미래 카드 청구를 파생하되 미래 정답(`future_scenario`)은 미사용
4. 일부 할부 건은 카드사 명세의 월별 회차별 정확 금액/수수료가 없어 승인금액 균등 분할 **추정** 및 경고로 표시
5. 스크린샷 구매 후보는 확정 지출이 아니고, 시뮬레이션 이벤트로 명시적으로 선택할 때만 반영
6. 저축 목표에 해당하는 독립 컬렉션이 없음; 저장 목표를 가정하지 않고 `[]` + 경고로 반환
7. 원본 거래에 실제 `refund`, `atm_withdrawal` kind가 없음; 각각 별도의 테스트 fixture로 검증. 이 fixture는 제공된 원본 JSON에 저장하지 않음
8. 미래 신용카드 명세, ML 입력자료와 가상투자 시세는 생성 시뮬레이션이며 검증된 실생활 거래가 아님
9. 독립 금융 함수 계산은 날짜별 보수적 최저잔고(같은 날짜 출금 선반영)와 마감잔고를 구분. 계좌별 특정 시각의 거래순서를 완전히 재현한 것은 아님

## 기존 소스 검토
- GitHub `MOAAPP/MOA_2ND`, 기본 브랜치 `main`, React 18 + Vite 5 + TypeScript, PWA
- 기존 Express/tsx API 포트 8787, Vite `/api` 프록시, 클라이언트의 localStorage 및 자체 금융 상태
- `src/lib/moa/ledger.ts`, `billing.ts`, `risk.ts`, `forecast.ts`, `src/lib/cashflow.ts` 등 기존 계산 엔진과 `src/lib/worldWallet.ts` 별도 가상 지갑
- 최상위 홈은 사용하지 않는 예전 `src/screens/Home.tsx`가 아니라 `src/App.tsx`의 **`V8Home`** (`src/screens/V8Finance.tsx`)
- 화면/라우팅/투자·학습/기존 카드뉴스와 Express API를 유지. 새 데모 금융 카드만 Python API에 연결

## 직접 검증
- Python 3.13, FastAPI TestClient, `cd backend && pytest -q`: **22 passed**
- 공통 데이터 20명 모두 11/04 마감 스냅샷 일치, 사용자 분리, 승인/청구·할부·취소/환불 fixture·ATM fixture·이체·급여 지연·가상 자산·예측 입력·가상 입력·정합성·반복 계산/결측 경고
- GitHub Actions Python backend / `npm ci` / `npm run build` / `npm test` 모두 성공 (액션 실행 #41834964253)

## 적용 경계
**이번 1단계가 완전히 교체한 것은 신규 S01 데모 카드의 잔액 계산 영역뿐**. 기존 UI 전체의 현금흐름 차트와 독립 TS 시뮬레이션까지 Python 단일 소스로 교체한 것은 아님. 운영 PWA와 연결하려면 FastAPI 서비스 별도 배포 및 빌드타임 `VITE_FINANCE_API_BASE` 지정 필요. 실사용자 금융정보 도입 전 인증/권한 분리 필수.