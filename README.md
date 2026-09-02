# ☕ 음료조사 (drink-survey)

회의 전에 부서원 음료 주문을 취합하는 사내 웹앱. FastAPI + SQLite + Jinja2 서버 렌더.
라즈베리파이에 띄우고 Tailscale로 접근하는 구성을 전제로 만들었다.

## 빠른 시작 (로컬/개발)

```bash
# 파이썬 가상환경 설정
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt        # 파이에선 --break-system-packages 필요할 수 있음
cp .env.example .env                    # DEV_LOGIN=1 이 켜져 있음
python seed.py --admin-email you@company.co.kr --admin-name 당신이름 --demo
bash run.sh                             # http://127.0.0.1:8080
```

개발 모드에서는 로그인 화면의 **dev 로그인**에 등록된 이메일을 넣으면 구글 없이 들어간다.
`--demo`는 예시 카페·그룹·조사를 만들어 준다.

## 스모크 테스트

```bash
python smoke_test.py        # 21개 체크: 로그인·트리·응답·게스트·lazy 마감·자동 채택·스케줄
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

- **회원**: 관리자가 이메일로 사전 등록 → 본인 최초 구글 로그인 때 `google_sub` 바인딩(active).
  미등록 이메일·다른 계정 바인딩 시도는 거절.
- **그룹 트리**: `parent_group_id`로 본부-팀. 본부 조사의 대상은 유효 멤버(직속 ∪ 하위 팀, 중복 제거).
- **lazy 마감**: 스케줄러 없음. 마감시각 지난 뒤 첫 접근이 마감 + 미응답자 자동 채택
  (즐겨찾기 → 카페 공통 기본음료 → 제외). `active` 멤버만 대상.
- **lazy 스케줄 생성**: 요일 당일 첫 접근이 그 주 조사 생성. 마감 지난 채 첫 접속이면 그 주 건너뜀.
  멱등성은 `UNIQUE(schedule_id, survey_date)`가 보장.
- **1인 1잔**: 재응답은 덮어쓰기. 게스트 잔은 `allow_guests`일 때 누구나 추가(추가자 표시).
- **가격 스냅샷**: 응답 시점 가격을 `final_price`에 저장 → 이후 메뉴 가격이 바뀌어도 과거 조사 금액 보존.

## 운영 배포 (라즈베리파이 + Tailscale)

### 1) 구글 OAuth 설정
GCP 콘솔 → API 및 서비스 → OAuth 클라이언트 ID(웹 애플리케이션) 생성.
- 승인된 리디렉션 URI: `https://<기기명>.<tailnet>.ts.net/auth/callback`
- 발급된 client id/secret을 `.env`에 넣고 `ALLOWED_DOMAIN=회사도메인`, `DEV_LOGIN=0` 으로.

### 2) systemd 유닛 (앱)
`/etc/systemd/system/drink-survey.service`:
```ini
[Unit]
Description=Drink Survey
After=network.target

[Service]
WorkingDirectory=/home/pi/drink-survey
EnvironmentFile=/home/pi/drink-survey/.env
ExecStart=/usr/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now drink-survey
```

### 3) Tailscale Funnel
```bash
sudo tailscale funnel --bg 8080
```
그러면 `https://<기기명>.<tailnet>.ts.net` 공개 URL이 뜬다(TLS 자동). Funnel이 TLS를 종단하고
앱에는 http로 넘기므로 세션 쿠키는 `https_only=False`로 둔다.

> ⚠️ Funnel URL은 부서에만 공유해도 기술적으로는 공개 URL이다. 로그인(구글, 회사 도메인 한정)이
> 그 앞을 막는 실질적 접근 제어다. 사내망에 상시 외부 터널을 두는 구성이니 보안 정책은 별도 확인 권장.

### 백업
SQLite 파일 하나이므로 백업은 `cp drink_survey.db 백업위치` (또는 `sqlite3 .backup`). cron으로 하루 1회 NAS에 복사 권장.

## 다음(v2 후보)
결제자 뽑기 · 정산 기록 · 마감 전 리마인더 · 복수 카페 후보 투표 · HTMX로 무새로고침 갱신.
