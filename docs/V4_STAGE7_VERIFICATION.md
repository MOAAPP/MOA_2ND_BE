# 평행통장 V4.0 — 7단계 기술 통합 점검 기록 (2026-11-05)

> 검증 대상: React `MOAAPP/MOA_2ND`, FastAPI `MOAAPP/MOA_2ND_BE`, 업로드 원본 20명 합성 데이터 ZIP. 이 문서는 설계상 구현 여부와 실제 통과한 테스트를 구별합니다.

## 기준 리비전

- React main 기준 SHA: `5b570d2ee9fa87dd515345f4a76819b0da92e8d0`
- React 6단계 코드 기준 SHA: `ae6dcbe1f1073405da6a5f6e48018ca258e105f4`
- FastAPI main 기준 SHA: `1872c01332af11c2e34ab4df37c67feb2723b02f`
- FastAPI 6단계 코드 기준 SHA: `9d827e4c0e8b22bb00523eb92ee296739b209490`
- 본 7단계 브랜치는 **각 저장소의 6단계 브랜치에서 시작**. Main을 강제 갱신하지 않음.

## 금융 원본 감사 (업로드 파일을 실제 Python으로 검사)

- 합성 데이터 ZIP SHA-256: `9e2adc5a7aa7720ef8daf24f8766389233dee399c0ea0826f9dc3a90aaeab609`
- 메타데이터: `MOA-20PERSONAS-92D-HIGH-FIDELITY-JSON-v3`, 스키마 3.1.0
- 사용자: 정확히 S01~S20, 20명. 계좌 40개, 카드 40개, 일정 154개
- 거래: 12,777건, 거래 ID 중복 0건, 전체 사용자별 컬렉션에서 알 수 없는 사용자 ID 0건
- 관측 범위: 2026-09-11~2026-11-04 (55일); 2026-11-05~2026-12-11은 미관측 가정 데이터
- S01 기본: 2026-11-04 결제계좌 `S01-PAY` 잔액 **27,800원**; 월세 350,000원. S01은 20명의 프로필 중 하나.
- 2026-11-04 현재 20명 잔액:
  `S01=27800 S02=118400 S03=61800 S04=83800 S05=21700 S06=116200 S07=98200 S08=894100 S09=118500 S10=50334 S11=118900 S12=119800 S13=114300 S14=87700 S15=7900 S16=93300 S17=26300 S18=1180800 S19=110700 S20=58900`
- 합성 JSON 메타데이터는 **기존 앱과 직접 가져오기 호환되지 않는다**고 명시함. 별도 FastAPI 데이터 매핑 필요.
- `purchase_intent_screenshots` 67건은 실제 이미지가 아닌 메타데이터이며 `image_attached=false`인 경우가 존재함. 이것만으로 멀티모달 인식 정확도를 평가하지 않음.

## 7단계에서 수정한 실제 코드

1. React `src/components/Stage3Finance.tsx`: 홈 API와 선택적 투자 시세 API 호출 독립화; 시세 API가 실패해도 원본 홈 상태를 먼저 출력. 사용자/날짜에 묶이지 않은 과거 시세·저장 경로 응답을 가림. 조건을 바꿨는데 이전 계산 경로가 화면에 잠깐 남는 상태도 방지.
2. React `scripts/validate-stage3-browser.mjs`: 실제 브라우저에서 시세 API 503을 강제하고도 S01 계좌 잔액이 표시되는지를 검사.
3. FastAPI `app/api/finance.py`: `dataset-snapshot?as_of=` 지원; 관측된 거래·잔액확인·가상투자·구매 후보를 기준일로 자르고 시나리오 미래/오라클 자료 제외.
4. FastAPI `tests/test_stage7.py`: S01 기본 홈/20명 잔액/과거 컷오프/Swagger 핸들러 테스트.
5. CI: React 브라우저 테스트가 같은 stage7 백엔드 코드를 체크아웃하도록 연결. Python CI에 stage7 회귀 테스트 포함.
6. 각 README: 체크아웃할 정확한 브랜치, 실행 순서, API와 시연 한계 기록.

## 기획서 대비 기능 상태 (정적 코드 조사와 테스트를 분리)

| 기능 | 코드 조사 | 본 단계 검증 상태 |
|---|---|---|
| 결제 문자 구조화 | Stage2 규칙 파서/API 존재 | 추가 E2E 미검증 |
| 멀티모달 상품 인식 | 서버 비전 LLM 호출 구현 존재 | 실 이미지·API 키 없어 정확도 미측정 |
| 구매/미구매·구매 연기 | Stage3 여섯 경로 Python 엔진 | 기존 단위 테스트 존재, 새 CI 결과 대기 |
| 가상 저축/가상 투자·혼합 | Stage3 시뮬레이션 코드 | 실제 UI/금액 E2E CI 확인 필요 |
| 통합 금융 계산 | Python 금융 모듈·테스트 | 회귀 테스트 재실행 결과 대기 |
| 미래 변동 지출 ML | LightGBM 오프라인 학습·최근7일/요일 기준선 | 모델 학습/MAE 재측정 필요 |
| LLM Tool Calling | OpenAI 호환 도구 호출 코드·Python 경로탐색 | API 키 없는 환경에서 실호출 미검증 |
| 제약조건 경로 탐색 | Python 제한 탐색·검증 함수 | 유효 경로 발견률 실측 필요 |
| 소비 여유·모닝 브리핑 | Python Stage6 서비스/API | 변화 입력 연계 E2E 검증 필요 |
| 시나리오 저장·계획 추적 | Stage3/5/6 JSON 런타임 저장·조회 | 저장 후 실거래 변화 연동 미확인 |
| Home/Parallel Worlds/Virtual Assets/Insights | Stage6 React 코드 존재 | Chromium CI 재실행 결과 대기 |
| 사용자 전환/관측일/잔액 | API/React 데이터 연결 코드 존재 | 20명 데이터 감사 수행, 추가 CI 대기 |

**중요:** 현재 단계에서 메인 데이터 로딩 문제의 두 가지 원인을 확인: (a) `main` 백엔드에는 Stage3~6 엔드포인트가 아직 통합되지 않음, (b) 홈 UI가 선택적 시세 API에 의존함. (b)는 수정; (a)는 Stage6 기반 작업 브랜치와 PR로 해결을 제안하지만 PR이 병합되기 전 기본 `main`은 그대로임.

**미완료:** 문자 저장 결과가 다음 모든 API(Home, ML, agent, briefing)에 동일하게 반영되는 상태 오버레이의 E2E 검증, 실제 LLM 호출 결과와 비용, 50건 상품 이미지 비교실험, 합성 데이터 밖에서의 모델 MAE, 투자 실시간 시세, 뉴스 제공자 연결. 실사용 금융 성과 주장 금지.

## Mac 최초 설치

```bash
mkdir -p ~/Projects && cd ~/Projects
git clone https://github.com/MOAAPP/MOA_2ND_BE.git
cd MOA_2ND_BE
git fetch origin
git switch fix/v4-stage7-integration-audit-20261105
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q tests/test_stage7.py
python -m app.ml.train
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

모델 학습이 없는 최초 호출도 baseline으로 동작하며 실제 LightGBM 선택 여부는 `app/data/models/evaluation.json`에서 확인. 키는 셸에서 `export OPENAI_API_KEY='...'` 및 `export MOA_AGENT_API_KEY='...'`로 설정(선택); 실제 값을 Git에 기록하지 않음.

### Swagger

- http://127.0.0.1:8000/docs
- http://127.0.0.1:8000/openapi.json
- http://127.0.0.1:8000/api/v4

### 최초 API 확인 순서

1. `GET /api/v4/health` → users=20, snapshot_date=2026-11-04
2. `GET /api/v4/demo/default` → user_id=S01, as_of_date=2026-11-04
3. `GET /api/v4/users/S01/home?as_of=2026-11-04` → available_cash=27800, 거래·현금흐름 길이 존재
4. `GET /api/v4/users/S02/home?as_of=2026-11-04` → available_cash=118400, S02 ID
5. `GET /api/v4/users/S01/dataset-snapshot?as_of=2026-10-01` → 미래일 거래·투자·구매 후보 없음
6. `GET /api/v4/users/S01/forecast?as_of=2026-11-04` → model_type/forecasts/daily, forecast는 실제 실행 여부 확인
7. `GET /api/v4/users/S01/briefing?as_of=2026-11-04` → spending_capacity/summary
8. `POST /api/v4/scenarios/compare` → OpenAPI가 제공하는 `WorldCompareRequest` 입력으로 여섯 경로/최저 잔액 확인
9. `POST /api/v4/agent/analyze` → LLM 서버 키가 있을 때만 실제 모델 호출 가능. 키 없을 때 503은 의도한 명시 오류
10. `POST /api/v4/vision/analyze` → 실제 이미지를 올리고 비전 API 키가 있을 때만 평가

## 프론트 실행 (별도 터미널)

```bash
cd ~/Projects
git clone https://github.com/MOAAPP/MOA_2ND.git
cd MOA_2ND
git fetch origin
git switch fix/v4-stage7-integration-audit-20261105
npm ci
unset VITE_FINANCE_API_BASE
npm run dev:web
```

React: http://localhost:5173 · 기존에 안내한 배포 주소: https://parallel-bank.vercel.app (7단계 브랜치 배포 여부는 확인되지 않음).
React는 Vite 프록시 `/api/v4`로 Mac FastAPI에 요청. 서버를 종료하면 20명 데이터는 숨기지 않고 명시적 오류를 보여야 하며, 임의 더미 잔액으로 대체하면 안 됨.

## 완료 판정

PR/CI 통과 + Mac에서 실제 Swagger 및 React 화면 확인 + 사용자 전환 + 날짜 변경 + 6경로 + 거래 상태 변경 이후 전 화면 일치가 되어야 최종 완료. 이 문서나 코드 존재만으로 완료 아님.
