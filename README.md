> **V4.0 7단계 통합 검증 브랜치 (2026-11-05)**  
> `fix/v4-stage7-integration-audit-20261105`를 체크아웃하세요. 이 브랜치는 6단계 FastAPI/ML/에이전트 코드 위에 7단계 과거 기준일 조회·20명 메인 잔액 회귀 테스트를 추가합니다. 기본 `main`에는 아직 3~6단계 기능이 통합되지 않았으므로 `main` 실행 결과만으로 기능이 없다고 판단하지 마세요.  
> 최초 설치: `python3.12 -m venv .venv && source .venv/bin/activate && python -m pip install -r requirements.txt`  
> 테스트: `python -m pytest -q tests/test_stage7.py tests/test_stage6.py tests/test_stage3.py tests/test_ml.py tests/test_agent.py tests/test_finance.py`  
> 모델 학습: `python -m app.ml.train` (선택, 데이터가 충분한 날짜에서만)  
> 서버: `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`  
> Swagger: http://127.0.0.1:8000/docs · 데이터: http://127.0.0.1:8000/api/v4/demo/default · 홈: http://127.0.0.1:8000/api/v4/users/S01/home?as_of=2026-11-04  
> LLM 비전 인식은 서버 `OPENAI_API_KEY`, 에이전트는 서버 `MOA_AGENT_API_KEY`가 필요하며, 키를 넣지 않은 상태에서 성공했다고 주장하지 않습니다. 테스트 데이터는 실사용자 금융 데이터가 아닌 20명 합성 자료입니다.  
> 실행 검증 현황·한계: [docs/V4_STAGE7_VERIFICATION.md](docs/V4_STAGE7_VERIFICATION.md)

# 평행통장 V4.0 — Python 금융 계산 백엔드

이 저장소는 평행통장의 **금융 계산 백엔드 (Python 3.10+ / FastAPI)** 전용입니다.

- 프론트엔드 (React + Vite + PWA): [MOAAPP/MOA_2ND](https://github.com/MOAAPP/MOA_2ND)
- 백엔드 (FastAPI, Pydantic, pytest): 이 저장소
- 데이터베이스 없음. 20명 합성 금융 데이터 ZIP을 **원본 변경 없이 읽기 전용**으로 사용합니다.
- 신용카드 승인·청구, 잔액 스냅샷, 계좌 이체, 날짜별 출금, 필수결제 및 3/7/14일 잔액 시뮬레이션
- 4단계 LightGBM 오프라인 실제 학습·요일별/최근7일 기준선 비교·FastAPI 추론을 지원하며, 미래 확정 결제와 변동 출금을 분리합니다.

## 4단계 LightGBM 실제 학습 및 메인 데이터 연결

현재 `feat/v4-stage4-lightgbm-cashflow` 브랜치를 체크아웃하세요. 반드시 **학습**과 **서버 시작**을 순서대로 실행하세요.

```bash
python -m pip install -r requirements.txt
python -m app.ml.audit
python -m app.ml.train
pytest -q
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```


기본 사용자 `S01`, 관측 기준일 `2026-11-04`, 20명 전환이 가능합니다. React는 별도 터미널에서 `npm ci && npm run dev:web`를 사용합니다.

- 실제 원본 데이터 사용 확인: http://127.0.0.1:8000/api/v4/health
- 기본 시연 사용자: http://127.0.0.1:8000/api/v4/demo/default
- 홈 화면 실제 금융 데이터: http://127.0.0.1:8000/api/v4/users/S01/home?as_of=2026-11-04
- 변동 지출 예측: http://127.0.0.1:8000/api/v4/users/S01/forecast?as_of=2026-11-04
- 모델 반영 현금흐름: http://127.0.0.1:8000/api/v4/users/S01/forecast-cashflow?as_of=2026-11-04&scenario=base
- Swagger: http://127.0.0.1:8000/docs

학습 산출물 `app/data/models/` 아래의 LightGBM 모델 TXT, metadata, evaluation JSON 및 원본 데이터 감사 JSON은 Git 추적에서 제외됩니다. GitHub Actions에서는 학습·평가 보고서를 워크플로 아티팩트로 보관합니다. 모델 파일이 없거나 LightGBM이 선정되지 않았다면 API는 기준선과 `model_type`을 구분하여 반환합니다.

**관측된 55일 이외의 미래 37일 시나리오 데이터는 학습 및 테스트의 정답이 아닙니다.** 지출 증가 25%는 경험적 가정이며 검증된 확률이나 예측구간이 아닙니다. 합성 데이터 점수로 실사용자 정확도를 주장하지 않습니다.

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

- API 시작 주소: http://127.0.0.1:8000/ (자동으로 Swagger UI로 이동)
- Swagger: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- OpenAPI JSON: http://127.0.0.1:8000/openapi.json
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

