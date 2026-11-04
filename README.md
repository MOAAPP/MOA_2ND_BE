# 평행통장 V4.0 — Python 금융 계산 백엔드

이 저장소는 평행통장의 **금융 계산 백엔드 (Python 3.10+ / FastAPI)** 전용입니다.

- 프론트엔드 (React + Vite + PWA): [MOAAPP/MOA_2ND](https://github.com/MOAAPP/MOA_2ND)
- 백엔드 (FastAPI, Pydantic, pytest): 이 저장소
- 데이터베이스 없음. 20명 합성 금융 데이터 ZIP을 **원본 변경 없이 읽기 전용**으로 사용합니다.
- 신용카드 승인·청구, 잔액 스냅샷, 계좌 이체, 날짜별 출금, 필수결제 및 3/7/14일 잔액 시뮬레이션
- ML·LLM의 미래 예측 결과를 만들어내지 않습니다. 2단계에서 별도의 예측/시뮬레이션 이벤트를 주입할 수 있습니다.

## 로컬 실행 (macOS)

**Python 3.10 이상 필요** (권장 3.12). macOS 기본 제공 Python 3.9는 지원하지 않습니다.

```bash
# 최초 1회: Homebrew를 이미 사용 중이라면 Python 3.12 설치
brew install python@3.12

# 최초 1회: GitHub 백엔드 프로젝트 복제
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/MOAAPP/MOA_2ND_BE.git
cd MOA_2ND_BE

# 이후 매번: 자동 Python 버전 검사, 프로젝트 전용 .venv 설치, 서버 실행
bash scripts/dev-local.sh
```

이미 저장소를 복제했다면 clone 단계를 반복하지 마세요. `cd ~/Projects/MOA_2ND_BE && git pull && bash scripts/dev-local.sh`로 시작합니다.

**로컬 API 검증**

- API 시작 주소: http://127.0.0.1:8000/ (자동으로 Swagger UI로 이동)\n- Swagger: http://127.0.0.1:8000/docs\n- ReDoc: http://127.0.0.1:8000/redoc\n- OpenAPI JSON: http://127.0.0.1:8000/openapi.json
- Health: http://127.0.0.1:8000/api/v4/health
- Cashflow: http://127.0.0.1:8000/api/v4/users/S01/cashflow?horizon_days=14
- Tests: `.venv/bin/python -m pytest -q`

## 프론트엔드 연동 / CORS

프론트엔드 저장소: [MOAAPP/MOA_2ND](https://github.com/MOAAPP/MOA_2ND) · 프론트 통합 PR: [#33](https://github.com/MOAAPP/MOA_2ND/pull/33).

두 번째 터미널에서 프론트엔드 저장소가 없다면 `cd ~/Projects && git clone https://github.com/MOAAPP/MOA_2ND.git`. 이후:

```bash
cd ~/Projects/MOA_2ND
git fetch origin
git switch feature/v4-finance-fastapi-stage1-20261104
npm ci
npm run dev
```

- React Vite: `http://localhost:5173`
- FastAPI 직접: `http://127.0.0.1:8000`
- Vite 개발 프록시: `/api/v4/*` → FastAPI 8000, 그 외 `/api/*` → 기존 Node API 8787. `npm run dev`는 Node와 Vite를 둘 다 시작합니다.
- 브라우저에서는 `http://localhost:5173/api/v4/health`로 프록시를 검증하세요.
- **CORS:** 개발 프록시는 브라우저 기준 동일 출처이므로 별도 CORS 설정이 필요하지 않습니다. 직접 FastAPI로 요청하는 경우 백엔드는 기본적으로 `http://localhost:5173`, `http://127.0.0.1:5173` 두 출처를 허용합니다. GET 및 JSON POST preflight를 자동 테스트합니다.
- **중요:** 프론트엔드의 `VITE_FINANCE_API_BASE`를 Cloudtype이나 다른 배포 주소로 설정해두었다면 로컬 테스트에서는 비워두고 Vite 서버를 재시작하세요.

## 원본과 재현성

`app/data/source/moa_20users_financial_test_JSON.zip`은 업로드 받은 **합성 테스트 데이터**이며 실사용자 자료가 아닙니다. 초기 CI에서 정확한 원본 ZIP을 해시로 검증해 백엔드 저장소에 보존합니다. 다른 파일을 쓰려면 `MOA_DATASET_PATH`를 명시하세요. 원본을 재작성하거나 없는 거래를 채워 넣지 않습니다.

SHA-256 (원본 ZIP): `9e2adc5a7aa7720ef8daf24f8766389233dee399c0ea0826f9dc3a90aaeab609`

## 문서 및 API

[API 실행 및 계산 규칙](docs/V4_STAGE1_API.md) · [데이터 분석 및 정합성 검증](docs/V4_STAGE1_DATA_AUDIT.md)

금융 데이터셋은 합성 데이터를 이용한 검증용입니다. 인증 없는 시연 API로 실데이터를 서비스하지 마세요. 운영 계정 조회 및 사용자 쓰기 기능은 인증·인가, 보안, 접근 격리를 마련한 뒤 도입해야 합니다.

## 개발 정책

현재 개발은 **무료 로컬 우선**으로 진행합니다. GitHub Actions는 Python 금융 테스트만 자동 실행하고 Cloudtype 배포는 실행하지 않습니다. 프론트엔드 공개 Vercel 주소에서 로컬 FastAPI에 연결하는 것은 불가능합니다(요청하는 기기의 localhost는 맥북이 아님). API는 로컬 Vite 환경에서 확인하세요.

