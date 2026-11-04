from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.finance import router
from app.core.settings import CORS_ORIGINS

app = FastAPI(
    title='평행통장 V4 금융 계산 엔진',
    version='4.0.0-stage1',
    description='Read-only synthetic source, cash snapshot, explicit card settlement, deterministic forward projection. No ML model or LLM in stage 1.',
)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=['GET','POST'],
                   allow_headers=['Content-Type'], allow_credentials=False)
app.include_router(router)