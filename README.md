# ☕ 음료조사 (drink-survey)

회의 전에 부서원 음료 주문을 취합하는 사내 웹앱. FastAPI + SQLite + Jinja2 서버 렌더.
라즈베리파이에 띄우고 Tailscale로 접근하는 구성을 전제로 만들었다.

## 빠른 시작 (로컬/개발)

Python **3.9 이상** (RHEL 기본 3.9에서 동작 확인).

```bash
# 파이썬 가상환경 설정
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt        # 파이에선 --break-system-packages 필요할 수 있음
cp .env.example .env                    # DEV_LOGIN=1 이 켜져 있음
python seed.py --admin-email you@company.co.kr --admin-name 당신이름 --demo
bash run.sh                             # http://127.0.0.1:8080  (uvicorn app.main:app 으로 직접 띄워도 .env를 읽는다)
```

개발 모드에서는 로그인 화면의 **dev 로그인**에 등록된 이메일을 넣으면 구글 없이 들어간다.
`--demo`는 예시 카페·그룹·조사를 만들어 준다.

## 스모크 테스트

```bash
python smoke_test.py        # 56개 체크: 로그인·트리·응답·게스트·lazy 마감·자동 채택·스케줄
```

## 구조

```
app/
  main.py            앱 조립, 세션 미들웨어, 라우터 등록
  config.py          환경변수 설정
  db.py              SQLite 연결·초기화, 시간 헬퍼
  schema.sql         테이블 9개 (users, cafes, menus, groups, group_members,
                     user_cafe_defaults, survey_schedules, surveys, survey_responses)
  models.py          pydantic — 메뉴 옵션 JSON 검증
  services.py        도메인 로직: 그룹 트리(재귀 CTE), lazy 마감·자동채택,
                     lazy 스케줄 생성, 주문서 집계
  deps.py            로그인/관리자 의존성, 템플릿 렌더
  routers/           auth, home, cafes, surveys, schedules, me, admin
  templates/         Jinja2
seed.py              초기 관리자·데모 데이터
```

## 핵심 설계 (설계서와 일치)

> 상세: [docs/architecture.md](docs/architecture.md) · [docs/design.md](docs/design.md) · [docs/implementation.md](docs/implementation.md)

- **회원**: 관리자가 이메일로 사전 등록 → 본인 최초 구글 로그인 때 `google_sub` 바인딩(active).
  미등록 이메일·다른 계정 바인딩 시도는 거절.
- **그룹 트리**: `parent_group_id`로 본부-팀. 본부 조사의 대상은 유효 멤버(직속 ∪ 하위 팀, 중복 제거).
- **마감 + 자동 채택**: 앱 안의 1분 주기 스케줄러(`services.tick`)가 마감시각에 조사를 닫고 미응답자를 자동 채택
  (즐겨찾기 → 카페 공통 기본음료 → 제외). `active` 멤버만 대상. 사용자 접근 시에도 같은 처리가 걸려(lazy) 스케줄러가 죽어도 동작.
- **주간 조사 생성**: 스케줄 요일 0시 첫 tick이 그 주 조사를 만든다. 멱등성은 `UNIQUE(schedule_id, survey_date)`가 보장.
- **텔레그램 알림**(선택): 조사 생성·마감 때 그룹 채팅으로. chat_id 없는 그룹은 가장 가까운 상위 → 없으면 하위 그룹으로.
- **1인 1잔**: 재응답은 덮어쓰기. 게스트 잔은 `allow_guests`일 때 누구나 추가(추가자 표시).
- **가격 확정 시점 = 마감**: 열린 조사는 메뉴를 고치면 응답 금액이 바로 따라가고, 마감 순간의 값이 `final_price`로 고정된다.
- **메뉴 JSON 내보내기/가져오기**: 카페 간 복사용. 가져오기는 관리자만, 이름 기준 갱신·추가만(삭제 없음), pydantic 검증.

## 운영 배포 (라즈베리파이 + Tailscale)

### 1) 구글 OAuth 설정
GCP 콘솔 → API 및 서비스 → OAuth 클라이언트 ID(웹 애플리케이션) 생성.
- 승인된 리디렉션 URI: `https://<기기명>.<tailnet>.ts.net/auth/callback`
- 발급된 client id/secret을 `.env`에 넣고 `ALLOWED_DOMAIN=회사도메인`, `DEV_LOGIN=0` 으로.

### 2) systemd 유닛 (앱)
`/etc/systemd/system/drink-survey.service` — `/home/pi/drink-survey`와 `pi`를 실제 경로·사용자로 바꿀 것.
**uvicorn은 venv 안에 있으므로 절대경로로** (systemd는 PATH를 보지 않는다):
```ini
[Unit]
Description=Drink Survey
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/drink-survey
ExecStart=/home/pi/drink-survey/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --limit-concurrency 32 --timeout-keep-alive 5 --no-server-header
Restart=always

[Install]
WantedBy=multi-user.target
```
`.env`는 앱이 직접 읽으므로 `EnvironmentFile`은 필요 없다(넣으면 그 값이 `.env`보다 우선).
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now drink-survey
systemctl status drink-survey --no-pager -l     # 실패하면 journalctl -u drink-survey -n 30
```
- `status=203/EXEC` + `Failed to locate executable` → `ExecStart` 경로가 틀림.
- 경로가 맞는데도 `203/EXEC` → RHEL 등 SELinux enforcing. `sudo ausearch -m avc -ts recent`로 확인 후
  `sudo chcon -R -t bin_t <프로젝트>/.venv/bin` 또는 프로젝트를 `/opt`로 이동.

### 3) Tailscale Funnel
```bash
sudo tailscale funnel --bg 8080
```
그러면 `https://<기기명>.<tailnet>.ts.net` 공개 URL이 뜬다(TLS 자동).

### 보안 체크리스트
- `.env`: `SESSION_SECRET` 무작위 값, `DEV_LOGIN=0`, `ALLOWED_DOMAIN` 설정. `DEV_LOGIN=0`인데 시크릿이 기본값이거나
  OAuth 설정이 없으면 **앱이 기동을 거부한다**(의도된 동작).
- `chmod 600 .env drink_survey.db` — 둘 다 비밀(세션 서명 키, 회원 이메일).
- `DEV_LOGIN=0`이면 `/docs`·`/openapi.json`이 꺼지고 세션 쿠키에 `Secure`, 응답에 HSTS가 붙는다.
  비로그인 공개 경로는 `/login`, `/auth/*`, `/logout`만 남는다.
- uvicorn `--limit-concurrency 32 --timeout-keep-alive 5 --no-server-header`: 느린 연결 붙잡기 방어, 서버 소프트웨어 노출 제거.
- 퇴사자 접근 회수: 회원 관리 → **비활성화**. 이미 로그인된 브라우저도 다음 요청부터 로그아웃된다.
- 의존성은 `>=`로 열려 있다. 운영 설치 후 `pip freeze > requirements.lock`으로 고정해 두고 업데이트는 의식적으로.
- `.ts.net` 도메인은 인증서 투명성 로그에 올라가 봇이 찾아온다. "비공개 URL"이 아니다.
- 인터넷 노출 자체를 없애려면 Funnel 대신 `tailscale serve`(tailnet 내부 전용). 단, 부서원 전원이 Tailscale을 써야 한다.

> ⚠️ Funnel URL은 부서에만 공유해도 기술적으로는 공개 URL이다. 로그인(구글, 회사 도메인 한정)이
> 그 앞을 막는 실질적 접근 제어다. 사내망에 상시 외부 터널을 두는 구성이니 보안 정책은 별도 확인 권장.

### 4) 텔레그램 알림 (선택)
1. 텔레그램 @BotFather → `/newbot` → 토큰을 `.env`의 `TELEGRAM_BOT_TOKEN`에.
2. 봇을 부서 그룹 채팅에 초대. chat_id는 `https://api.telegram.org/bot<토큰>/getUpdates` 응답의 `chat.id`(그룹은 음수).
3. 관리자 → 그룹 관리에서 해당 그룹에 chat_id 입력. `APP_URL`을 Funnel 주소로 넣으면 메시지에 링크가 붙는다.

### 업데이트 (스키마가 바뀐 버전으로 올릴 때)
```bash
sudo systemctl stop drink-survey
git pull
python migrate.py --dry-run     # 무엇이 바뀌는지 확인
python migrate.py               # <DB>.bak-<시각> 백업 후 schema.sql 기준으로 재구성. 실패하면 자동 롤백
sudo systemctl start drink-survey
```
`migrate.py`는 멱등이라 스키마 변경이 없는 업데이트에 돌려도 무해하다. 컬럼 추가·제약 변경은 전부 `schema.sql`만 고치면 따라온다.

### 백업
SQLite 파일 하나이므로 백업은 `cp drink_survey.db 백업위치` (또는 `sqlite3 .backup`). cron으로 하루 1회 NAS에 복사 권장.

## 다음(v2 후보)
결제자 뽑기 · 정산 기록 · 마감 전 리마인더 · 복수 카페 후보 투표 · HTMX로 무새로고침 갱신.
