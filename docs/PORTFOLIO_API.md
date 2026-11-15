# 가상 다종목 포트폴리오 API

## 호출

`POST /api/v4/portfolio/simulate`

이 API는 기존 `/api/v4/scenarios/compare` (6가지 평행세계)를 변경하지 않는 **독립적인 가상 포트폴리오 계산 API**입니다. 프론트에서 연동하기 전까지 기존 화면 동작은 그대로입니다.

```json
{
  "user_id": "S01",
  "as_of": "2026-11-04",
  "horizon_days": 14,
  "minimum_required_balance": 0,
  "forecast_daily_spend_override": 0,
  "valuation_loss_percent": 20,
  "trades": [
    {
      "trade_id": "buy-etf",
      "code": "ETF-A",
      "asset_type": "etf",
      "side": "buy",
      "trade_date": "2026-11-05",
      "quantity": 2,
      "unit_price": 10000
    },
    {
      "trade_id": "buy-stock",
      "code": "STOCK-B",
      "asset_type": "stock",
      "side": "buy",
      "trade_date": "2026-11-06",
      "quantity": 3,
      "unit_price": 20000
    },
    {
      "trade_id": "sell-etf",
      "code": "ETF-A",
      "asset_type": "etf",
      "side": "sell",
      "trade_date": "2026-11-09",
      "settlement_date": "2026-11-10",
      "quantity": 1,
      "unit_price": 12000
    }
  ],
  "price_marks": [
    { "code": "ETF-A", "date": "2026-11-12", "unit_price": 9000 },
    { "code": "STOCK-B", "date": "2026-11-12", "unit_price": 15000 }
  ]
}
```

## 응답에서 프론트가 사용할 주요 필드

- `positions[]`: 종목명 대신 **코드**, stock/etf 종류, 수량, 남은 원가, 평균단가, 현재 평가액, 실현/미실현/총손익
- `daily_chart[]`: 날짜별 결제계좌 잔액, 거래 미실행 기준 대비 잔액 차이, 투자 평가액, 손익 (그래프)
- `trades[]`: 매수·매도 금액과 정산일
- `total_buy_amount`, `total_sell_proceeds`, `settled_sell_proceeds`, `unsettled_sell_proceeds`: 매매·정산 내역
- `net_payment_account_cash_impact`: 검증 기간 안에 결제계좌에서 빠져나간 가상 매수 대금 + 실제 정산일이 도달한 가상 매도 대금
- `cashflow_validation_status`, `minimum_payment_account_balance`, `essential_payments_covered`, `first_shortfall_date`: 현금흐름 제약 검증
- `warnings[]`: 테스트 가격, 정산 가정, 가상 손실, 미정산 대금, 계좌 부족 위험 등

## 계산 및 안전 가정

1. 합성 사용자의 **기존 가상 보유 종목을 새 매매의 원금이나 현금으로 가져오지 않습니다.** 이번 요청의 가상 매수부터 보유 수량이 생깁니다. 공매도·잔고 초과 매도는 HTTP 422.
2. 매수는 거래일에 결제계좌에서 한 번 차감하고, 매도대금은 `settlement_date`에 한 번 입금합니다. 정산일 생략 시 가상 샌드박스 편의상 당일 정산으로 간주하고 경고를 표시합니다. 조회 기간 뒤 정산될 금액은 현금에 포함하지 않습니다.
3. 모든 종목 가격은 **사용자가 입력한 가정**입니다. `price_marks[]`가 없으면 마지막 사용자 매매 단가를 그대로 유지합니다. 실제·실시간·미래 예측 주가가 아닙니다.
4. 각 매도 손익은 이동평균 매입원가법으로 계산합니다. 거래수수료·세금·배당금은 포함하지 않습니다.
5. `valuation_loss_percent`는 자산 **평가 금액에만** 적용합니다. 가상 매매대금, 이미 실현한 손익, 결제계좌 잔액을 다시 차감하지 않습니다.
6. 결제계좌 현금흐름은 기존 Python `finance.calculate`로 계산하고, 같은 기준의 거래 없는 결과와 비교합니다. 잔액이 부족하더라도 결과를 숨기지 않으며 **안전한 투자 추천이라고 표시하지 않습니다.**
7. 최대 90일만 검증합니다. 이 응답은 투자 수익을 보장하거나 자동 매수·매도를 수행하지 않으며, 실제 계좌 정보를 변경하지 않습니다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_portfolio.py tests/test_stage3.py -q
```
