# MOA EC2 + Vercel + AI: 공모전 시연용 보안 배포

## 적용 순서

1. 백엔드 작업 PR을 병합한 후 EC2에서 최신 코드로 갱신합니다.
2. 아래 설치 스크립트로 랜덤 서버 전용 토큰, SQLite 호출 한도, 안전한 Docker 재배포를 진행합니다.
3. EC2에 생성된 `.env.gateway`의 `MOA_GATEWAY_SECRET` 값을 Vercel `parallel-bank`의 서버 전용 `MOA_PROXY_SHARED_KEY`에 똑같이 등록합니다.
4. Vercel `MOA_FASTAPI_ORIGIN`에 EC2 HTTPS origin을 등록하고, `VITE_FINANCE_API_BASE`는 삭제하거나 빈 값으로 둡니다.
5. 프론트엔드 PR을 병합하고 **parallel-bank 프로젝트에 새 배포를 실시합니다.**
6. 별도 발급한 AI API 키를 EC2의 `.env.gateway`에 등록하고 Docker 컨테이너를 재생성합니다.
7. 브라우저에서 금융 화면과 AI 기능을 검증합니다.

이 설정은 **공개 공모전 시연용**이지 고객 개인정보/은행 계좌를 처리하는 상용 보안 구조가 아닙니다. 합성 사용자 20명만 사용해야 합니다.

## EC2 재배포

EC2의 터미널에서:

```bash
cd ~/MOA_2ND_BE
git pull --ff-only
bash scripts/deploy-ec2-gateway.sh
```

- 스크립트는 8001번 로컬 포트에서 새 이미지를 검증한 뒤 기존 8000번 컨테이너를 교체합니다.
- **기존 컨테이너 내부에만 저장한 가상 거래·시나리오는 새 컨테이너에 자동 이전되지 않습니다.** 필요한 경우 배포 전에 백업하세요.
- 생성되는 `.env.gateway`는 Git에 업로드하지 않습니다.
- `moa-persist` Docker volume이 요청 한도와 일부 런타임 데이터를 보존합니다.
- 백엔드 보안 규칙: `/api/v4/health` 이외의 `/api/v4/*`는 기본적으로 401, Vercel 서버에서 인증 헤더를 보내면 200.
- `/docs`와 `/openapi.json`은 읽기 전용으로 공개되지만 직접 Swagger Try it out은 기본적으로 401입니다. 실제 API는 Vercel 프록시로 호출합니다.
- 80/443 포트는 Caddy 공개용, 22번은 기존 제한된 SSH 범위만, **8000번은 외부 공개 금지**.

## 서버 비밀 토큰 확인

서버에서만:

```bash
cd ~/MOA_2ND_BE
sed -n 's/^MOA_GATEWAY_SECRET=//p' .env.gateway
```

이 값은 Vercel Dashboard → `parallel-bank` → Settings → Environment Variables → `MOA_PROXY_SHARED_KEY`에만 붙여넣습니다. 채팅/메신저/깃허브에는 공유하지 않습니다.

```text
MOA_FASTAPI_ORIGIN=https://3-107-190-215.sslip.io
MOA_PROXY_SHARED_KEY=<EC2에서 만든 비밀 토큰>
```

`MOA_GATEWAY_SECRET`와 `MOA_PROXY_SHARED_KEY`가 같아야 합니다.

## 실제 AI 연결

ChatGPT Plus 구독으로는 OpenAI API 호출 요금이 제공되지 않습니다. API 키와 과금은 제공업체에서 별도로 설정합니다. OpenAI 호환 게이트웨이를 사용한다면 `MOA_AI_BASE_URL`, `MOA_AI_MODEL`을 그 서비스가 지원하는 값으로 바꿉니다.

키가 없다면 AI 없이 기존 **금융 엔진/규칙기반 학습/ML**은 동작합니다.

EC2에서 아래 명령은 키를 화면·쉘 히스토리에 출력하지 않고 저장합니다.

```bash
cd ~/MOA_2ND_BE
read -rsp 'AI API key (입력값 숨김): ' AI_KEY
echo
printf 'MOA_AI_API_KEY=%s\n' "$AI_KEY" >> .env.gateway
unset AI_KEY
chmod 600 .env.gateway
```

키를 변경할 때는 `.env.gateway` 안의 기존 `MOA_AI_API_KEY` 라인을 먼저 제거해야 중복이 생기지 않습니다. 값은 절대 공개 저장소에 넣지 마세요.

Docker의 `--env-file`은 컨테이너 생성 시 값이 고정되므로 변경 후에는 실행 중인 컨테이너를 단순 `restart`해도 새 키가 들어오지 않습니다. 다음 명령으로 재생성합니다:

```bash
sudo docker rm -f moa-backend
sudo docker run -d --name moa-backend --restart unless-stopped \
  --env-file "$(pwd)/.env.gateway" \
  -v moa-persist:/var/lib/moa \
  -p 127.0.0.1:8000:8000 moa-backend:gateway
```

## 확인

```bash
curl -fsS http://127.0.0.1:8000/api/v4/health
sudo docker ps
```

브라우저에서는 금융 앱 Vercel 도메인:

```text
https://parallel-bank.vercel.app/api/v4/health
https://parallel-bank.vercel.app/api/v4/ai/status
https://parallel-bank.vercel.app/api/v4/users/S01/home?as_of=2026-11-04
```

JSON에 `llm_configured: true`가 보이면 서버가 API 키를 읽었다는 뜻이며, **실제 LLM 호출 성공 여부는 별도 테스트해야 합니다.** 상위 제공업체 계정의 프로젝트 예산·하드 지출 한도를 가능한 경우 함께 설정하세요.

### 사용량 보호

- `MOA_AI_DAILY_MAX_REQUESTS=100`: 전체 유료 가능 호출 후보의 하루 요청 수 최대치
- `MOA_AI_PER_CLIENT_HOURLY_MAX=12`: 클라이언트별 시간당 제한
- SQLite 파일은 `moa-persist` 볼륨에 위치합니다.
- 고가 AI 모델/긴 프롬프트로 실행 시 **요청 횟수 제한이 곧 금액 제한은 아닙니다.** 공급자 콘솔의 사용량 알림/예산을 활용하세요.
- 대량 사용자·실데이터·상용 서비스로 확장하려면 사용자 인증·계정별 권한·DB 접근제어·WAF·지속 로그 및 결제 한도를 추가하세요.

### IP와 도메인 안정성

EC2의 일반 공인 IP는 인스턴스를 중지/시작하면 변경될 수 있습니다. `sslip.io` 호스트가 바뀌면 Vercel `MOA_FASTAPI_ORIGIN`을 갱신해야 합니다. 상용 안정성은 고정 도메인 + 네트워크 구성이 필요합니다.
