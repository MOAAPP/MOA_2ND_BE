# 평행통장 V4.0 — Python 금융 계산 백엔드

이 저장소는 평행통장의 **금융 계산 백엔드 (Python 3.10+ / FastAPI)** 전용입니다.

- 프론트엔드 (React + Vite + PWA): [MOAAPP/MOA_2ND](https://github.com/MOAAPP/MOA_2ND)
- 백엔드 (FastAPI, Pydantic, pytest): 이 저장소
- 데이터베이스 없음. 20명 합성 금융 데이터 ZIP을 **원본 변경 없이 읽기 전용**으로 사용합니다.
- 신용카드 승인·청구, 잔액 스냅샷, 계좌 이체, 날짜별 출금, 필수결제 및 3/7/14일 잔액 시뮬레이션
- ML·LLM의 미래 예측 결과를 만들어내지 않습니다. 2단계에서 별도의 예측/시뮬레이션 이벤트를 주입할 수 있습니다.

## 로컬 실행

```bash
git clone https://github.com/MOAAPP/MOA_2ND_BE.git
cd MOA_2ND_BE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

- API 문서: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json
- 상태 확인: http://localhost:8000/api/v4/health
- 테스트: `python -m pytest -q`

## 프론트엔드 연동

프론트 로컬 Vite `/api/v4`만 `localhost:8000`으로 프록시합니다. 기존 Node 서버 `:8787`의 시장/모의투자/Discord API는 변경하지 않습니다. 배포 환경에서는 FE 빌드 환경변수 `VITE_FINANCE_API_BASE=https://<deployed-fastapi-host>`를 지정하고, BE의 `MOA_CORS_ORIGINS=https://parallel-bank.vercel.app`에 실제 프론트 origin을 허용해야 합니다. **호스팅·환경변수 설정은 아직 완료되지 않았습니다.**

## 원본과 재현성

`app/data/source/moa_20users_financial_test_JSON.zip`은 업로드 받은 **합성 테스트 데이터**이며 실사용자 자료가 아닙니다. 초기 CI에서 정확한 원본 ZIP을 해시로 검증해 백엔드 저장소에 보존합니다. 다른 파일을 쓰려면 `MOA_DATASET_PATH`를 명시하세요. 원본을 재작성하거나 없는 거래를 채워 넣지 않습니다.

SHA-256 (원본 ZIP): `9e2adc5a7aa7720ef8daf24f8766389233dee399c0ea0826f9dc3a90aaeab609`

## 문서 및 API

[API 실행 및 계산 규칙](docs/V4_STAGE1_API.md) · [데이터 분석 및 정합성 검증](docs/V4_STAGE1_DATA_AUDIT.md)

금융 데이터셋은 합성 데이터를 이용한 검증용입니다. 인증 없는 시연 API로 실데이터를 서비스하지 마세요. 운영 계정 조회 및 사용자 쓰기 기능은 인증·인가, 보안, 접근 격리를 마련한 뒤 도입해야 합니다.
