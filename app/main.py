"""평행통장 금융 API — FastAPI OpenAPI / Swagger 문서 제공."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.finance import router
from app.api.stage2 import router as sms_router, purchase_router
from app.api.stage3 import router as stage3_router
from app.api.ml import router as ml_router
from app.api.agent import router as agent_router
from app.api.stage6 import router as stage6_router
from app.core.settings import CORS_ORIGINS

app = FastAPI(
    title="평행통장 V4 · 금융 계산 API",
    version="4.0.0-stage6",
    summary="합성 금융 데이터를 이용한 결제계좌 현금흐름 계산·시뮬레이션",
    description=(
        "## 시작하기\n"
        "1. **GET /api/v4/health**: 서버·데이터셋 연결을 확인합니다.\n"
        "2. **GET /api/v4/users**: 시연용 합성 사용자 ID를 조회합니다.\n"
        "3. **GET /api/v4/users/S01/cashflow**: S01의 3/7/14일 잔액을 확인합니다.\n"
        "4. **POST /api/v4/cashflow/simulate**: 가상 구매 이벤트를 추가하여 현금흐름을 비교합니다.\n"
        "5. **POST /api/v4/sms/analyze**: 문자 입력을 구조화합니다.\n"
        "6. **POST /api/v4/vision/analyze**: 실제 비전 LLM으로 상품 캡처를 판독합니다.\n\n"
        "**기준일:** 2026-11-04 (합성 금융 데이터 관측 종료일). "
        "미래에 실제로 발생한 거래를 예측에 섞지 않습니다. "
        "현재는 개발·시연용 API이며 인증 없는 실사용자 금융 데이터 서비스로 사용하면 안 됩니다.\n\n"
        "**API 문서:** [Swagger UI](/docs) · [ReDoc](/redoc) · [OpenAPI JSON](/openapi.json)"
    ),
    openapi_tags=[{
        "name": "v4-finance",
        "description": "결제계좌 상태·고정 결제·신용카드 청구·미래 현금흐름과 구매 시나리오 API",
    }, {
        "name": "v4-stage2-sms", "description": "규칙 기반 SMS 파싱·사용자 확인·거래 저장·잔액 반영",
    }, {
        "name": "v4-stage2-vision", "description": "비전 LLM 분석·구매 후보 저장·실거래와 독립된 가상 시뮬레이션",
    }, {"name": "v4-stage3-digital-twin", "description": "20명 공통 데이터 기반 홈·6경로 비교·저장·ML 입력"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)
app.include_router(router)
app.include_router(sms_router)
app.include_router(purchase_router)
app.include_router(stage3_router)
app.include_router(ml_router)
app.include_router(agent_router)
app.include_router(stage6_router)


@app.get("/", include_in_schema=False)
def swagger_home():
    """루트 주소를 Swagger 문서로 안내해 'Not Found' 혼란을 막습니다."""
    return RedirectResponse(url="/docs", status_code=307)
