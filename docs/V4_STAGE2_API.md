# 평행통장 V4.0 개발 2단계 — 로컬 FastAPI / Swagger

## 실제 구현 / 동작 범위

- 서비스는 React + Vite와 Python + FastAPI 2개 프로세스, 공통 합성 20명 원본 ZIP으로 동작합니다. 새 DB 없음.
- 원본 ZIP은 SHA256 검증 및 읽기 전용, 추가 기록은 `app/data/runtime/S01.json` 등 사용자별 JSON 오버레이입니다. 이 경로는 Git에서 제외되며 **개발용 단일 프로세스** 전제입니다.
- SMS는 **모든 형식을 규칙 기반으로만 파싱(LLM 호출 0회)** → 중복·불확실성 표시 → 수정·확인 → 거래 overlay 저장입니다. 비정형 SMS는 사용자가 직접 필드를 확인하며 외부 AI로 전송하지 않습니다.
- 쇼핑몰 이미지 업로드는 PNG/JPEG/WebP, 8 MB, 최대 1800만 화소 검증 후 EXIF를 제거하고 **실제** OpenAI 비전 API를 호출합니다. 분석 실패·키 없음은 오류입니다. OCR 규칙 결과로 성공을 가장하지 않습니다.
- 3단계 이전의 실제 구매는 별도 수동 기록입니다. 구매 후보를 확정해도 계좌 잔액이 바뀌지 않습니다. 가상 시뮬레이션 API를 사용해야만 해당 계산의 `simulation_events`에 반영됩니다.
- **보안 주의:** 지금은 실사용 서비스가 아닌 인증 없는 로컬 합성 데모입니다. 실계좌 SMS를 공개 API로 받지 마세요. 카드·계좌 번호, 주민번호 등 입력을 최소화하세요.

## 로컬 실행

백엔드 폴더에서 시작하고, 사용자의 Mac에서 방금 설정한 `OPENAI_API_KEY`를 동일한 터미널에서 유지하세요.

```bash
git fetch origin
git switch feature/v4-stage2-sms-vision-20261105
bash scripts/dev-local.sh
```

스크립트는 Python 3.10+ (우선 3.12)를 골라 `.venv`를 준비하고 `pip install -r requirements.txt` 후 다음 명령으로 실행합니다:

```bash
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

키가 존재하는지 출력 없이 확인하려면 `test -n "$OPENAI_API_KEY" && echo "키 있음"`.
FastAPI 자체는 키 없이도 금융·문자 규칙 파서·Swagger를 실행합니다.
Swagger는 http://127.0.0.1:8000/docs, OpenAPI JSON은 http://127.0.0.1:8000/openapi.json, API 루트는 http://127.0.0.1:8000 입니다.

프론트엔드는 **별도의 기존 저장소** MOAAPP/MOA_2ND의 `feature/v4-stage2-sms-vision-20261105` 브랜치이며, 기존 1단계 프론트 브랜치 위에서 만들었습니다.

```bash
git fetch origin
git switch feature/v4-stage2-sms-vision-20261105
npm ci
npm run dev
```

`npm run dev`는 기존 Node Express API(:8787)와 Vite(:5173)를 동시에 실행합니다. Vite `/api/v4` 요청만 FastAPI(:8000)로 프록시됩니다. 다른 `/api`는 기존 Node 기능을 유지합니다. 접속 http://localhost:5173 (로컬). Vercel의 공용 배포에서 사용자의 localhost FastAPI로 직접 요청하지 못합니다.

## Swagger 2단계 검증 순서

1. `GET /api/v4/health` → 200, `status=ok`, `users=20`, `synthetic=true`.
2. `GET /api/v4/users/S01/state` → 원본 snapshot 기준 계좌 정보.\n   `GET /api/v4/users/S01/dataset-snapshot` → 공통 20명 ZIP의 S01 원본 관측 거래·일별 잔액·고정 일정(프론트 홈의 주 데이터).
3. `POST /api/v4/sms/analyze`에 아래 JSON → rule parser, `llm_called=false`, `transactions[0]` 중복 여부와 `uncertainty` 확인:

```json
{
  "user_id": "S01",
  "text": "[Web발신]\nKB국민체크 승인\n18,500원\n2026/11/05 12:10\n배달의민족",
  "use_llm_for_unknown": false
}
```

4. `POST /api/v4/sms/confirm`: 3번의 `transactions[0]` **전체 객체**를 `transaction`에 붙여넣고 아래 형식으로 실행:

```json
{
  "user_id": "S01",
  "transaction": {
    "source_id": "3번 응답 source_id",
    "fingerprint": "3번 응답 fingerprint",
    "user_id": "S01",
    "kind": "debit_approval",
    "issuer": "KB국민",
    "payment_method": "debit",
    "merchant": "배달의민족",
    "amount": 18500,
    "occurred_at": "2026-11-05T12:10:00+09:00",
    "debit_at": "2026-11-05T12:10:00+09:00",
    "card_id": null,
    "installments": null,
    "transaction_status": "posted",
    "uncertainty": [],
    "duplicate_possible": false,
    "parsing_source": "rules",
    "confidence": 0.9
  },
  "account_id": null,
  "acknowledge_unresolved": true
}
```

`source_id`와 `fingerprint`는 메시지 해시이므로 **반드시 3번에서 응답한 실제 값을 복사**해야 합니다. 같은 확정 거래를 재등록하면 409 `duplicate_transaction`입니다.
5. `GET /api/v4/users/S01/confirmed-transactions` → 확인한 거래만 반환.
6. `GET /api/v4/users/S01/confirmed-state?as_of=2026-11-05` → 현재 시점 등록 거래를 원본과 분리해 계산. 다른 S02와 비교하면 사용자 데이터가 섞이지 않아야 합니다.
7. `POST /api/v4/vision/analyze` → Swagger에서 `user_id=S01`, `privacy_acknowledged=true`, `file`에 PNG/JPEG/WebP 업로드. 키가 없다면 `503 vision_api_key_missing`, 호출이 성공하면 `source=multimodal_llm`, `analysis_status=succeeded`. **실제 메인 상품, 판매가, 추천 상품, 옵션 증거를 이미지와 대조해야 합니다.**
8. `POST /api/v4/purchase-candidates/confirm` → 7번의 `analysis_id` 및 `product` 객체를 사용합니다. 사람이 명확히 확인한 상품명, 정가, 판매가, 수량, 옵션 추가금, 배송비를 수정합니다. 없는 금액을 0으로 입력하려면 실제로 무료임을 확인해야 합니다. `shipping_confirmed=true`, `confirm_total=true`. `status=candidate`, `actual_balance_impact=0` 확인.
9. `GET /api/v4/users/S01/purchase-candidates` → 사용자별 후보 조회, 원본 데이터 불변.
10. `POST /api/v4/users/S01/purchase-candidates/{candidate_id}/simulate`에 `{"debit_at":"2026-11-06T12:00:00+09:00","payment_method":"debit","horizon_days":14}` 입력 → `daily_cashflow.events`에 `transaction_status=simulated`, 실제 기준 현금흐름은 불변.

## 오류 코드

- 404: 사용자/상품 분석/후보 없음.
- 409: 중복 거래 확정.
- 413: 이미지 용량/픽셀 제한.
- 415: 허용되지 않은 이미지 확장자/MIME.
- 422: 입력 타입·날짜·계좌·상품 합계 검증 실패.
- 429: 모델 요청 한도.
- 502: AI 응답 스키마·네트워크 오류.
- 503: 데이터셋 누락·AI 키 없음·AI 인증 실패.
- 504: AI 호출 시간 초과.

검증은 `python -m pytest -q`와 `python scripts/eval_stage2.py`. 평가 JSONL이 50건보다 적으면 미확인 건수를 그대로 보고합니다. 이미지나 원본 개인정보를 공개 GitHub에 넣지 않습니다.

## 다음 단계(API/모델 계약)

`app/schemas.py::CalculationRequest`의 `forecast_events`와 `simulation_events`를 유지합니다. 3단계 시뮬레이터는 `PurchaseCandidate`에서 확인된 `estimated_checkout_total`과 명시적인 출금일을 받아 `ExternalEvent(status="simulated", source="simulation")`로 변환해야 합니다. 미래 기본 예측값에는 포함하지 않습니다. 신용카드의 실제 대금 출금일은 금융기관 청구서 확인 후 확정합니다. 멀티모달 분석 `VisionAnalysis.product`는 가격 발견 결과이며 실제 거래가 아닙니다.

## 구현되지 않았거나 제한된 부분

실제 비전 API 호출의 성공률·속도·토큰 비용은 로컬 키와 50장 정답 이미지가 없어 미측정입니다. 프론트엔드 기존 로컬 원장 및 Python SMS 오버레이의 완전한 단일 원장화는 후속 통합 작업으로 남습니다. 로컬 JSON 오버레이는 별도 사용자 인증/공유 서버 동시성에 적합하지 않습니다. 공용 배포는 기존 Node API 중심이며 로컬 FastAPI와 자동 연결되지 않습니다.
