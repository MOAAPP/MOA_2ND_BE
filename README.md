# MOA 금융 백엔드

평행통장(MOA)의 FastAPI 금융 계산·합성 데이터·ML·학습·AI Agent 백엔드입니다.

## 현재 운영 구조

- 프론트엔드: [parallel-bank.vercel.app](https://parallel-bank.vercel.app) (Vercel / `MOAAPP/MOA_2ND`)
- 백엔드: [EC2 HTTPS 헬스](https://3-107-190-215.sslip.io/api/v4/health) (AWS EC2, Docker `moa-backend`, Caddy)
- API: `/api/v4/*`; EC2의 비공개 API는 서버 간 인증 토큰이 필요합니다.
- 이 저장소의 `main` 병합 → GitHub Actions `validate-main.yml` 성공 → EC2의 systemd timer → Docker 이미지 재빌드 및 안전한 교체
- Vercel에 공유 인증 키가 없으면 프론트엔드의 프록시는 기존 Vercel 호환 API로 폴백할 수 있습니다. 프론트 연결 여부를 별도로 검증하세요.

## 시작하기

```bash
python -m pip install -r requirements.txt
python -m pytest -q
uvicorn app.main:app --reload
```

Swagger(로컬): http://127.0.0.1:8000/docs

합성 시연 데이터는 실제 사용자 금융 기록이 아닙니다. 모델 설명은 엔진의 계산 결과를 근거로 작성하며, 유료 LLM 호출에는 서버 전용 API 키가 필요합니다.

## 문서

| 목적 | 문서 |
|---|---|
| 프론트와 API 계약 | [FRONTEND_API_CONTRACT.md](docs/FRONTEND_API_CONTRACT.md) |
| EC2 자동 배포 및 롤백 | [EC2_AUTO_DEPLOY.md](docs/EC2_AUTO_DEPLOY.md) |
| Vercel·EC2 인증 및 AI 연결 | [EC2_VERCEL_AI_DEPLOY.md](docs/EC2_VERCEL_AI_DEPLOY.md) |
| 운영 상태·오류·AI 호출 확인 | [OPERATIONS_MONITORING.md](docs/OPERATIONS_MONITORING.md) |
| ML 검증 근거 | [V5_FORECAST_EVAL.md](docs/V5_FORECAST_EVAL.md) |
| 데이터 품질 | [V4_STAGE1_DATA_AUDIT.md](docs/V4_STAGE1_DATA_AUDIT.md) |
| 통합 검증 | [V4_STAGE7_VERIFICATION.md](docs/V4_STAGE7_VERIFICATION.md) |

API 구현: `app/api/`, 금융 엔진과 AI: `app/services/`, ML: `app/ml/`, 회귀 테스트: `tests/`.

**운영 주의:** `.env.gateway`의 토큰과 API 키는 Git에 커밋하거나 브라우저 환경변수(`VITE_*`)에 입력하지 마세요.
