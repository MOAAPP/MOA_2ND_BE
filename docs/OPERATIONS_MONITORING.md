# MOA EC2 / Vercel 운영 모니터링

## 빠른 현황

- **운영 API:** https://3-107-190-215.sslip.io/api/v4/health — 200 / `status=ok`이면 외부 HTTPS와 FastAPI가 응답합니다. 이 결과만으로 AI 기능이나 최신 커밋이 검증되는 것은 아닙니다.
- **Vercel:** https://vercel.com/jihyuncodings-projects/parallel-bank → Observability / Logs, Deployments
- **GitHub Actions:** https://github.com/MOAAPP/MOA_2ND_BE/actions/workflows/validate-main.yml
- **프론트에서 실제 EC2를 통과했는지:** `curl -sS -D - -o /dev/null https://parallel-bank.vercel.app/api/v4/health` 결과의 `X-MOA-Backend`가 `ec2-gateway`여야 합니다. `legacy-compat`이면 기존 API를 사용 중입니다.
- **AI 사용 가능 여부:** API `/api/v4/ai/status`(프록시 연결 후 접근)를 통해 `llm_configured`, `agent_configured`, `vision_configured`만 확인합니다. 키 자체는 반환하지 않습니다.

## EC2 Instance Connect에서 확인

```bash
# 프로세스, 포트, 현재 배포된 코드
sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
cat ~/.moa-ec2-deployed-sha
curl -fsS http://127.0.0.1:8000/api/v4/health

# 마지막 30분 FastAPI 요청 및 런타임 오류
sudo docker logs --since 30m --tail 200 moa-backend 2>&1

# HTTPS 프록시(Caddy) 진단
sudo docker logs --since 30m --tail 100 moa-caddy 2>&1

# 최근 EC2 자동 배포 여부, 실패 이유, 예약 상태
sudo systemctl status moa-ec2-auto-deploy.timer --no-pager
sudo journalctl -u moa-ec2-auto-deploy.service --since '1 hour ago' --no-pager

# 개인 정보 없이 AI 모델 호출 상태와 지연 시간만 보기
sudo docker logs --since 1h moa-backend 2>&1 | grep 'moa_ai_provider_request' | tail -30
```

`moa_ai_provider_request`는 **모델 제공자를 실제로 호출했을 때만** 기록합니다. `status`는 `ok` 또는 안전한 오류 코드이고 `elapsed_ms`는 호출 소요 시간입니다. 원본 프롬프트, 금액, API 키, 개인식별자는 로그에 남기지 않습니다. 로그가 비어 있다고 AI가 정상이라는 뜻은 아닙니다. 호출이 없거나 키가 없어 호출이 시작되지 않았을 수도 있습니다.

## AI 키를 노출하지 않고 설정 여부 확인

```bash
sudo docker exec moa-backend python -c 'from app.services.ai_provider import configured; print({"text":configured(), "agent":configured("agent"), "vision":configured("vision")})'
```

이 명령어는 환경변수의 실제 **값을 출력하지 않습니다.** 키가 등록됐다는 사실만으로 AI 모델 호출 성공이 보장되는 것은 아닙니다.

## 장애 구분

| 현상 | 먼저 확인 |
|---|---|
| EC2 health 5xx / 접속 불가 | `docker ps`, `docker logs moa-backend`, `docker logs moa-caddy`, 디스크·메모리 |
| Vercel 응답에 `legacy-compat` | 서버 전용 `MOA_PROXY_SHARED_KEY`가 Vercel에 있는지 |
| Vercel 응답에 `ec2-gateway`인데 401 | EC2 `MOA_GATEWAY_SECRET`와 Vercel 키 일치 여부 (값은 로그/채팅에 출력 금지) |
| Vercel 응답에 502 | EC2 도메인/HTTPS/네트워크 및 Vercel Logs |
| AI 응답이 예시로 대체 | `/api/v4/ai/status`와 `moa_ai_provider_request`의 안전한 오류 코드 |
| AI 요청 429 | 시연용 전역·클라이언트별 일/시간 한도 확인 |
| 새 코드가 반영되지 않음 | GitHub main CI 성공, `journalctl`, 배포 SHA 파일 일치 여부 |

## 지속 모니터링에 대한 현재 한계

- Docker 로그와 systemd 로그는 운영자가 조회할 수 있지만, 별도의 통합 KPI 대시보드, AI 오류율 경보, 장기 보관은 아직 구축되지 않았습니다.
- Vercel Node AI는 개별 서버리스 인스턴스의 임시 통계만 가지므로 운영 전반의 영구 누적 KPI와 혼동하면 안 됩니다.
- EC2 FastAPI의 로그에서 수동으로 `elapsed_ms`를 분석할 수 있지만, **전체 p50/p95 지연 시간·품질 개선율은 아직 벤치마크 결과가 아닙니다.**
- AWS CloudWatch Logs 및 알람을 연동하면 컨테이너 로그와 오류·가용성 지표를 장기 보관하고 알림을 받을 수 있습니다. 현재 연동 여부는 별도 확인이 필요합니다.

## 보안 주의

`.env.gateway`, 키 값, 전체 `docker inspect` 환경변수, 인증 헤더를 채팅이나 공개 저장소에 붙이지 마세요. 운영 서버는 기존 localhost:8000 바인딩과 Caddy HTTPS 구조를 유지합니다.
