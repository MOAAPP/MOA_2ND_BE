# MOA AWS EC2 백엔드 자동 배포 (2026-11-14)

## 동작

GitHub 백엔드 저장소: https://github.com/MOAAPP/MOA_2ND_BE

| 단계 | 실행 주체 | 조건 |
| --- | --- | --- |
| 1. main에 push/PR merge | GitHub | 개발자가 소스 변경 |
| 2. pytest / 데이터셋 검증 | GitHub Actions hosted runner | validate-main.yml |
| 3. 성공한 main SHA 확인 | Ubuntu EC2 | 5분마다 systemd timer가 공개 GitHub Actions API 조회 |
| 4. Docker 빌드 및 사전 테스트 | Ubuntu EC2 | CI success와 현재 main SHA가 정확히 같을 때만 |
| 5. 기존 컨테이너 교체 | Ubuntu EC2 | 후보 컨테이너에서 health 200 / 무인증 401 / 인증 200 통과 |
| 6. 구 컨테이너 복원 | Ubuntu EC2 | 새 컨테이너가 정상 응답하지 않으면 롤백 |

- GitHub Secrets, SSH 개인키, 고정 공인 IP, AWS IAM 역할, inbound 22 전체 공개 불필요
- GitHub에 접속하는 아웃바운드 HTTPS(443)만 필요
- FastAPI는 여전히 EC2 localhost 8000, Caddy는 80/443으로 HTTPS 제공
- 기존 Caddy 설정과 별도 Docker 컨테이너를 변경하지 않음
- GitHub에서 테스트가 실패하면 배포하지 않음
- **신규 인스턴스에서 코드를 자동 설치하는 기능은 아님**: 지금 실행 중인 한 대의 EC2에 연결
- 배포는 push 즉시가 아닌 평균 수 분의 폴링 지연이 발생하며, EC2가 꺼져 있으면 작동하지 않음

## 최초 한 번 EC2에서 실행

AWS 콘솔의 EC2 Instance Connect 터미널에서:

```bash
cd ~/MOA_2ND_BE
git status --short
git pull --ff-only origin main
bash scripts/prepare-ec2-gateway.sh
sudo bash scripts/install-ec2-auto-deploy.sh
sudo systemctl start moa-ec2-auto-deploy.service
```

`git status --short`에 추적 중인 코드 수정이 있다면 자동으로 덮어쓰지 않습니다.

보안 환경파일 `.env.gateway`는 최초에 랜덤 토큰을 생성하며 GitHub에 전송하지 않습니다. 서버 사용자가 이미 설정한 `.env.gateway`가 있다면 그대로 유지됩니다.

`sudo systemctl start ...`는 GitHub Actions의 해당 main 커밋 검사가 아직 실행 중이면 안전하게 건너뜁니다. 이후 timer가 재시도합니다.

## 확인

```bash
sudo systemctl list-timers --all moa-ec2-auto-deploy.timer --no-pager
sudo journalctl -u moa-ec2-auto-deploy.service -n 80 --no-pager
cat ~/.moa-ec2-deployed-sha
sudo docker ps
curl -sS http://127.0.0.1:8000/api/v4/health
```

`cat ~/.moa-ec2-deployed-sha`는 첫 배포가 성공해야 존재합니다.

## 중요: Vercel / AI 키

현재 금융 웹 프론트 `parallel-bank.vercel.app`는 아직 **기존 금융 API 호환 서버**로 프록시할 수 있습니다. EC2에 자동 배포를 설정하는 것만으로 React 앱의 AI Agent가 AWS에 연결되지는 않습니다.

EC2를 프로덕션 API로 사용하려면:

- EC2 `.env.gateway`의 `MOA_GATEWAY_SECRET`과 Vercel 프로젝트의 서버 전용 `MOA_PROXY_SHARED_KEY`를 동일하게 설정
- Vercel `MOA_FASTAPI_ORIGIN`을 현재 EC2 HTTPS URL로 설정
- 별도로 발급한 AI API 키를 EC2 `.env.gateway`에 넣어야 실제 LLM/Agent/Vision 사용 가능
- 키를 등록한 후 새 Docker 배포가 한 번 필요함 (코드가 변경되지 않으면 폴러가 다시 배포하지 않으므로 `sudo rm ~/.moa-ec2-deployed-sha` 후 `sudo systemctl start moa-ec2-auto-deploy.service` 등 재배포 트리거 필요)

**API 키를 GitHub Actions 변수나 VITE_ 환경변수에 입력하지 마세요.** Docker 이미지는 키가 없이 빌드되고, 서버 전용 `.env.gateway`에서 실행 시 주입됩니다.

## 디스크/데이터/비용

- 후보 컨테이너는 임시 `moa-smoke` 볼륨을 쓰고 검증 뒤 제거합니다.
- 데이터 보존은 `moa-persist` 볼륨을 사용하며, 기존 컨테이너 내부 `/app/app/data/runtime` 폴더가 있으면 교체 전 `~/moa-backups`에 백업합니다. 다만 자동으로 새 스키마로 마이그레이션하지는 않습니다.
- 후보 또는 새 서비스 헬스 검사 실패 시 기존 컨테이너로 복구합니다. 첫 설치 때 기존의 `moa-backend`를 삭제하지 않고 이름을 바꿔 보관하므로 롤백 가능합니다.
- 19GB 루트 디스크에서 반복 Docker 빌드가 디스크를 채울 수 있습니다. 주기적으로 `sudo docker system df`를 확인하세요.
- EC2 서버는 실행 중 과금될 수 있으며, 폴링은 서버 추가 인스턴스를 만들지 않습니다.

## 트러블슈팅

```bash
sudo systemctl status moa-ec2-auto-deploy.service --no-pager
sudo journalctl -u moa-ec2-auto-deploy.service -n 150 --no-pager
sudo docker ps -a
sudo docker logs --tail 50 moa-backend
sudo docker logs --tail 50 moa-caddy
```

원격 공개 저장소의 커밋은 GitHub Actions의 성공 상태와 커밋 SHA가 일치해야 자동 배포됩니다. main에 무검증 코드를 병합하지 않도록 브랜치 보호(필수 검사 통과)를 권장합니다.

## 자동 배포 중단

```bash
sudo systemctl disable --now moa-ec2-auto-deploy.timer
```
