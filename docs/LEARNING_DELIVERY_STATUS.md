# 금융 학습·카드뉴스 실행 안내

## 변경 범위
수정·PR·배포 대상은 upxldxls/MOA2_test_FE와 upxldxls/MOA2_test_BE뿐이다.
MOAAPP/MOA_2ND, MOAAPP/MOA_2ND_BE는 읽기 전용이다.

## 데이터와 AI
- 금융 관측 종료일: 2026-11-04, 합성 사용자 S01~S20.
- 금융 계산은 기존 FastAPI 엔진을 사용한다. AI는 검증된 사실의 설명·문항·해설만 작성한다.
- 기존 MOA_AI_* 키/모델이 우선이다. 없을 때 명시적으로 MOA_AI_PROVIDER=vercel_gateway를 설정하면 Vercel OIDC로 호출한다.
- 실제 호출 성공은 source=llm, llm_used=true, model/status로 확인한다. 실패는 규칙 기반으로 표시하며 성공으로 위장하지 않는다.
- 생성된 문항·답안·해설과 AI 설명은 저장/캐시해 반복 호출을 줄인다.

## 저장과 발송
- 로컬: SQLite learning-state.sqlite3 (MOA_RUNTIME_DIR 아래), 여러 클라이언트가 같은 상태를 읽는다.
- 배포: 비공개 Vercel Blob, ETag 조건부 갱신(CAS), 사용자별 학습 문서와 기기별 카드 상태.
- 카드 PNG 렌더러는 기존 Node 코드를 재사용한다. 상태·이미지·구독·발송 이력은 FastAPI 저장 API에 보관한다.
- 기기별 lease로 동시 생성/발송을 직렬화한다. 외부 발송 전 sending 상태를 기록하며, 응답 유실 시 자동 재전송을 금지한다.
- Vercel Cron: 0 23 * * * UTC = 매일 08:00 Asia/Seoul. CRON_SECRET로 보호한다.
- Discord Webhook은 기존 값이 필요하다. 원본 Vercel 팀의 접근 권한이 없어 기존 값을 복구하지 못한 경우 실제 수신을 완료했다고 보고하지 않는다.
- 기사 검사는 RSS 출처·HTTPS URL·발행일·중복 메타데이터 검사이며 기사 내용의 사실 검증을 보장하지 않는다.

## 로컬 실행
BE: .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
FE: npm run dev
BE .env와 FE .env의 MOA_INTERNAL_SECRET을 동일하게 설정한다. FE의 MOA_FINANCE_API_BASE=http://127.0.0.1:8000, MOA_DURABLE_DELIVERY=on.
실제 로컬 AI 호출은 기존 제공자 키 또는 AI_GATEWAY_API_KEY 등 유효한 개발 자격 증명이 필요하다. 키는 저장소에 넣지 않는다.

## Swagger 검증 순서
/docs에서:
1. POST /api/v4/learning/storage-check → roundtrip_ok
2. 사용자 learning-interests → daily-quiz → daily-quiz/answer → learning-history
3. GET /api/v4/learning/quotes/{code} → POST /api/v4/learning/cardnews (autoSend=false)
4. GET /api/v4/learning/cardnews/{job_id} → 이미지·모델·연결 퀴즈 확인
5. PUT /api/v4/learning/subscription → POST /api/v4/learning/daily/preview
6. 실제 발송은 명시적 동의와 기존 Webhook 연결 후에만 검증

## 검사 결과
- FE 전체 43개 파일 / 334개 검사 통과, TypeScript/Vite/API 번들 빌드 통과.
- BE 학습·상태 저장·AI 제공자·Swagger 관련 36개 검사 통과.
- 실제 배포 저장/AI/Discord 결과는 PR 검증 기록과 최종 보고에서 별도 구분한다.

## 2026-11-14 배포 검증
- 일반 홈에서 퀴즈 답안 저장 후 학습 기록 반영, 새 FE 배포에서도 동일 답안 유지 확인.
- Swagger에서 실제 종목 시세 검증 후 카드 PNG 4장 생성 성공. 별도 FE 배포에서 저장된 4번째 PNG 재조회 성공.
- private Blob 읽기는 cache=0으로 origin 최신 상태를 사용한다. 응답 종료 전에 변경 저장과 lease 해제를 마친다. 조회 요청은 쓰기 lease를 잡지 않는다.
- 로컬 실제 HTTP: 저장소 왕복, 관심사, 퀴즈/답안/이력, 새 클라이언트 재조회, 카드 4장, 동일 체결 중복 생성 방지, PNG 조회 통과.
- 추가 회귀 검사: BE 36개, FE 관련 16개 통과. 기존 FE 전체 334개 통과 기록에 더해 타입 검사와 API 번들 빌드 재통과.
- 실제 AI 호출은 provider_auth_or_permission_failed. 연결 설정만으로 실제 모델 사용을 완료했다고 표시하지 않는다.
- test FE의 DISCORD_WEBHOOK_URL은 미설정. 기존 Discord 초대 링크는 유지했으며 Webhook으로 대체할 수 없다. 원본 Vercel 팀 권한 부재로 기존 비밀 설정 복구가 필요하다.
- 08:00 KST Cron 코드는 배포했다. 실제 Discord 자동 수신은 Webhook 연결 후 검증해야 한다.

## Discord 연결 보류 후 독립 학습 경로
- 수신 동의·구독 없이 POST /learning/daily/preview에서 카드 생성 가능. 입력 level/learningUserId/watchlist를 검증하며 구독 설정은 변경하지 않는다.
- 기존 렌더러를 재사용하고 생성 시점의 미리보기 입력을 저장해 재시작 후에도 동일 카드/퀴즈를 조회한다.
- 일반 홈 → 금융 카드뉴스 → 오늘 카드 미리 보기에서 실제 카드를 만든다. 샘플 화면과 구분한다.
- 카드가 없을 때에도 오늘의 퀴즈는 FastAPI 학습 이력과 연결한다. 발급 중 응답은 409와 사용자용 안내를 반환하며 재조회 버튼을 제공한다.
- 사용자의 2026-11-14 요청에 따라 Discord 연결·실제 발송 검증은 보류한다. 기존 목적지·동의 설정은 유지한다.

## 최종 검증 보완
- AI 실제 실패 원인을 provider_customer_verification_required로 확인했다. 계정 인증 또는 유효한 기존 키가 필요하며 추가 결제는 하지 않았다. Discord 연결/실제 발송 검증은 사용자 요청으로 보류한다.
- 배포 저장소 검사에서 4KB 문서를 같은 경로에 연속 2회 조건부 갱신하고 최신 값을 조회했다. origin 읽기에 identity 인코딩을 요청하고 CAS 충돌에 짧은 재시도 간격을 적용했다.
- 배포 UI에서 오답 저장·기준 해설·새로고침 유지 및 동일 카드 복구 확인. 로컬에서는 관심사를 바꿔도 미해결 오답이 다음 복습에 우선되는 것을 실제 HTTP로 확인했다.
- BE 관련 38개 검사 및 후속 저장 검사 7개 통과. FE 전체 335개 검사, 후속 SSE 정상 종료 검사를 포함한 서버 검사 15개 통과.
- 시세 계산은 유지하며 Vercel SSE를 플랫폼 제한 전에 정상 종료하도록 했다. 기존 EventSource 자동 재연결 및 갱신 대체 경로를 유지한다.
