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


## Cloudtype 자동 배포 (GitHub Actions)

[워크플로우](.github/workflows/deploy-main.yml)는 `main`의 코드 변경에 따라 다음 순서로 실행됩니다.

1. 원본 합성 데이터셋 SHA-256 검증
2. Python 3.12 의존성 설치 및 전체 `pytest` 실행
3. 성공하고 **GitHub Actions Secrets**의 `CLOUDTYPE_TOKEN`과 `GHP_TOKEN`이 모두 설정되어 있을 때만 Cloudtype `thotemily/moa-2nd-be` / `main` 배포

**처음 한 번 해야 하는 설정:** GitHub `MOAAPP/MOA_2ND_BE` → Settings → Secrets and variables → Actions → Repository secrets에 다음 두 값을 등록합니다.

- `CLOUDTYPE_TOKEN`: Cloudtype **API Key** (Deploy Key가 아님)
- `GHP_TOKEN`: Cloudtype 공식 GitHub Actions 통합에서 요구하는 GitHub Personal Access Token(권한 및 만료기한은 Cloudtype 공식 문서 참고)

토큰은 코드, 채팅, README, 커밋에 기록하면 안 됩니다. 토큰이 등록되지 않은 동안은 테스트만 실행되고 배포는 명시적으로 건너뜁니다.

**배포 구성:** Python 3.12, `pip install -r requirements.txt`, `uvicorn app.main:app --host 0.0.0.0 --port 8000`, `MOA_CORS_ORIGINS=https://parallel-bank.vercel.app` (Cloudtype **runtime `options.env`**, not `buildenv`). Cloudtype에서 발급되는 서비스 URL은 GitHub에 입력하지 않습니다. 프론트엔드 Vercel의 `VITE_FINANCE_API_BASE`에 해당 URL을 별도 등록해야 합니다.

공식 문서: https://docs.cloudtype.dev/ko/developers/githubactions
