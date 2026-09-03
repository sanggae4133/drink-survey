# 아키텍처

> 대상 독자: 이 코드를 처음 열어보는 개발자. "왜 이렇게 생겼나"를 10분 안에 파악하는 것이 목표.
> 설계 규칙은 [design.md](design.md), 파일·함수 단위 안내는 [implementation.md](implementation.md).

## 한 줄 요약

**단일 프로세스 서버 렌더 웹앱.** FastAPI + Jinja2 + SQLite 파일 하나. 별도 워커·큐·크론 없이,
시간에 따라 일어나야 할 일(마감, 주간 조사 생성)은 **같은 프로세스 안의 1분 주기 asyncio 태스크**가 처리하고,
사용자 요청 시점에도 같은 함수가 걸려(lazy) 태스크가 멈춰도 동작이 이어진다.

## 구성 요소

```
브라우저 ──https──▶ Tailscale Funnel (TLS 종단) ──http──▶ uvicorn :8080 ──▶ FastAPI 앱 ──▶ drink_survey.db (SQLite)
                                                                              │
                                                                              └──▶ Google OAuth (로그인 시에만)
```

| 구성 요소 | 역할 | 비고 |
|---|---|---|
| Tailscale Funnel | 공개 HTTPS URL, TLS 종단, 대역폭 제한 | 앱까지는 http. 세션 쿠키 `Secure`는 브라우저 기준이라 운영에서 켠다 |
| asyncio 태스크 (main.py) | 1분 주기 마감·조사 생성 | 같은 프로세스. 워커 1개 전제 |
| uvicorn | ASGI 서버 | systemd로 상주. 워커 1개 |
| FastAPI | 라우팅, 폼 파싱, 의존성 주입 | JSON API 없음. 전부 HTML 폼 POST → 303 리다이렉트 |
| Jinja2 | 서버 렌더 | JS는 주문 텍스트 복사 버튼 하나 |
| SQLite | 저장소 | 파일 1개. `PRAGMA foreign_keys=ON`. 백업 = 파일 복사 |
| Google OAuth (authlib) | 인증 | 사전 등록 이메일에만 계정 바인딩. 미설정 시 dev 로그인 폼으로 대체 |
| Telegram Bot API (httpx) | 알림 | 조사 생성·마감 때 그룹 채팅으로. 토큰 없으면 전체 비활성 |

## 레이어

```
routers/*.py      HTTP 경계. 폼 → 검증 → services 호출 → flash + 303 리다이렉트
services.py       도메인 로직. DB 커넥션을 받아 SQL 실행. HTTP를 모름
models.py         메뉴 옵션 JSON의 형태 검증(pydantic). 유일한 "스키마" 코드
db.py             커넥션 팩토리, 스키마 초기화, 시간 문자열 헬퍼
deps.py           FastAPI 의존성: 로그인 사용자, 관리자, 템플릿 렌더 + flash
schema.sql        DDL. 제약(UNIQUE, CHECK, FK)이 비즈니스 규칙의 상당 부분을 담당
```

원칙: **규칙은 가능한 한 DB 제약으로, 그 다음 services로, 라우터에는 HTTP 처리만.**
라우터가 직접 SQL을 쓰는 곳도 많은데(단순 CRUD), 그건 의도된 것이다. 한 번만 쓰이는 쿼리를 함수로 감싸지 않는다.

## 요청 한 번의 생애

```
1. SessionMiddleware가 서명된 쿠키에서 session dict 복원
2. 라우트 의존성 db_dep     → sqlite3 커넥션 1개 오픈 (요청 끝나면 close)
3. 라우트 의존성 current_user → session["user_id"]로 users 조회. 없으면 303 /login
4. (홈·조사 화면) lazy 처리: 스케줄 생성 → 마감 판정 → 자동 채택
5. 핸들러 본문: 폼 검증, SQL, flash 메시지
6. GET이면 render(): flash를 세션에서 pop해 템플릿에 넘김
   POST면 RedirectResponse(303)
```

커넥션은 요청 단위로 격리된다. `check_same_thread=False`는 FastAPI가 sync 의존성(스레드풀)과 async 핸들러(이벤트 루프)
사이에서 같은 커넥션을 넘기기 때문이지, 커넥션을 공유하려는 게 아니다.

## 시간 기반 동작: 인프로세스 스케줄러 + lazy 이중화

`main.py`의 lifespan이 `asyncio` 태스크 하나를 띄운다. 기동 직후 1회, 이후 60초마다 `services.tick()`을
스레드에서 실행한다(sqlite·httpx가 동기라서). 외부 크론·공개 트리거 엔드포인트는 없다.

| 해야 할 일 | 실행 주체 | 멱등성 보장 |
|---|---|---|
| 마감시각이 지난 조사를 닫고 미응답자 자동 채택 | tick(1분), 그리고 그 조사·홈을 누가 열 때 | `UPDATE ... WHERE status='open'` CAS. rowcount=1인 쪽만 자동 채택·알림 실행 |
| 스케줄 요일에 그 주 조사 생성 | tick(요일 0시 직후), 그리고 홈 접속 | `UNIQUE(schedule_id, survey_date)` + `INSERT OR IGNORE`. rowcount=1인 쪽만 알림 |

같은 함수를 스케줄러와 라우터가 함께 부르므로 tick과 사용자 요청이 동시에 와도 SQL 제약이 한 번만 통과시킨다.
tick이 예외로 실패해도 로그만 남기고 다음 분에 다시 돈다. 처음 설계는 lazy만 있었는데(누구도 접속 안 한 주는 조사가 안 생김),
"마감이 되면 반드시 자동 채택된 주문서가 나와야 한다"로 요구가 바뀌어 스케줄러를 넣었다. uvicorn 워커가 1개라는 전제가 붙는다.

## 텔레그램 알림

조사 생성(수동·스케줄)과 마감 시 `services.announce()`가 대상 채팅에 메시지를 보낸다. 대상은 `notify_targets()`가 정한다:
그룹 자신 → 가장 가까운 상위 순으로 chat_id가 있는 첫 그룹 하나. 위에 없으면 하위로 내려가며 각 가지의 첫 그룹들.
그래서 본부 채팅이 있으면 팀 조사도 본부 채팅 한 곳에만 가고, 본부에 없고 팀들에만 있으면 본부 조사가 팀 채팅마다 간다.
전송은 동기 `httpx.post`(5초 타임아웃), 실패는 경고 로그. 토큰이 없으면 함수가 즉시 반환해 개발·테스트에서는 아무 일도 없다.

## 동시성 모델

- uvicorn 워커 1개, SQLite 기본 잠금. 부서 단위(수십 명) 트래픽에 충분하다.
- 쓰기는 모두 `with db:` 블록(트랜잭션). 경쟁이 있을 수 있는 지점은 SQL 제약으로 막는다:
  - 마감 중복 실행 → status CAS
  - 조사 중복 생성 → UNIQUE 인덱스
  - 1인 1잔 → `UNIQUE(survey_id, participant_user_id)` (부분 인덱스, 게스트 잔 제외)
- 응답 저장은 `UPDATE → rowcount 0이면 INSERT`. 동시에 두 탭에서 첫 응답을 하면 한쪽이 UNIQUE 위반으로 500이 날 수 있다.
  실제로 일어나기 어려운 경우라 처리하지 않았다. 문제 되면 `INSERT ... ON CONFLICT DO UPDATE`로 바꾸면 된다.

## 인증·권한 경계

```
비로그인  → /login 만 접근 가능
member    → 카페·메뉴 등록/수정, 응답, 게스트 잔 추가, 내 즐겨찾기
          → 조사 생성·스케줄 등록은 자기가 유효 멤버인 그룹(소속 그룹과 그 상위)에만
disabled  → 모든 요청 거절(기존 쿠키 포함). 조사 대상·자동 채택에서도 제외
          → 본인이 만든 조사 마감 / 본인이 만든 스케줄 on·off / 본인이 추가한 게스트 잔 삭제
admin     → 위 전부 + 회원 사전 등록·수정, 그룹 트리·멤버 관리, 모든 조사 마감, 모든 게스트 잔 삭제
```

"카페와 메뉴는 누구나 수정"은 의도된 결정이다(위키 모델). 대신 `updated_by/updated_at`을 남긴다.

## 앱 수준 방어

| 위협 | 대응 |
|---|---|
| 세션 쿠키 위조 | itsdangerous 서명. 운영에서 기본 시크릿이면 기동 거부 |
| CSRF | 쿠키 `SameSite=Lax` + 상태 변경은 전부 POST. 토큰 없음 |
| XSS | Jinja2 자동 이스케이프. `menu_url`은 `http(s)://`만 허용(`javascript:` 차단). 사용자 데이터는 인라인 JS 문자열에 넣지 않고 `data-*` 속성으로 |
| SQL 인젭션 | 전부 파라미터 바인딩. f-string은 `?` 개수 생성에만 |
| 클릭재킹·MIME 스니핑 | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, 운영 HSTS |
| 거대 요청·깨진 입력 | Content-Length 64KB 초과 413. 옵션은 그룹·선택지 20개, 이름 40자, 금액 ±100만 원. 날짜·시각은 형식 검증 후 저장 |
| 퇴사자 접근 | `status='disabled'` → `current_user`가 매 요청 거절 |
| 관리자 잠금 | 자기 자신의 admin 해제·비활성화 불가 |
| 라우트 노출 | 운영에서 `/docs`, `/openapi.json` 비활성. `--no-server-header` |

받아들인 위험: 로그인한 회원은 소속과 무관하게 모든 조사·주문서를 열람할 수 있다(누가 뭘 마시는지). 부서 내부 도구라는 전제.

## 배포 토폴로지

라즈베리파이 1대 + systemd + Tailscale Funnel. 상세 절차는 README의 "운영 배포" 절.
Funnel URL은 기술적으로 공개 URL이므로 실질적 접근 제어는 **구글 로그인 + 회사 도메인 제한 + 사전 등록**이다.

## 하지 않은 것 (의도)

- APScheduler 같은 스케줄러 라이브러리 → `asyncio.sleep(60)` 루프 한 개면 충분
- ORM, 마이그레이션 도구 → 테이블 9개, `CREATE TABLE IF NOT EXISTS`로 충분. 스키마 변경이 생기면 그때 `ALTER` 스크립트를 추가
- JSON API, 프론트 프레임워크 → 폼 POST + 303이 모든 요구를 만족
- 사용자 자기 가입 → 사전 등록이 곧 접근 제어
- 옵션 테이블 정규화 → 메뉴 옵션은 JSON 한 컬럼. 조회 조건으로 쓰지 않으므로 정규화 이득이 없음
