# 아키텍처

> 대상 독자: 이 코드를 처음 열어보는 개발자. "왜 이렇게 생겼나"를 10분 안에 파악하는 것이 목표.
> 설계 규칙은 [design.md](design.md), 파일·함수 단위 안내는 [implementation.md](implementation.md).

## 한 줄 요약

**단일 프로세스 서버 렌더 웹앱.** FastAPI + Jinja2 + SQLite 파일 하나. 백그라운드 작업(스케줄러, 큐, 워커)이 없고,
시간에 따라 일어나야 할 일(마감, 주간 조사 생성)은 전부 **사용자의 HTTP 요청이 들어온 시점에 처리**한다.

## 구성 요소

```
브라우저 ──https──▶ Tailscale Funnel (TLS 종단) ──http──▶ uvicorn :8080 ──▶ FastAPI 앱 ──▶ drink_survey.db (SQLite)
                                                                              │
                                                                              └──▶ Google OAuth (로그인 시에만)
```

| 구성 요소 | 역할 | 비고 |
|---|---|---|
| Tailscale Funnel | 공개 HTTPS URL, TLS 종단 | 앱까지는 http. 그래서 세션 쿠키 `https_only=False` |
| uvicorn | ASGI 서버 | systemd로 상주. 워커 1개 |
| FastAPI | 라우팅, 폼 파싱, 의존성 주입 | JSON API 없음. 전부 HTML 폼 POST → 303 리다이렉트 |
| Jinja2 | 서버 렌더 | JS는 주문 텍스트 복사 버튼 하나 |
| SQLite | 저장소 | 파일 1개. `PRAGMA foreign_keys=ON`. 백업 = 파일 복사 |
| Google OAuth (authlib) | 인증 | 사전 등록 이메일에만 계정 바인딩. 미설정 시 dev 로그인 폼으로 대체 |

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

## 시간 기반 동작을 요청 시점에 처리하는 이유

크론이나 백그라운드 태스크가 없다. 대신:

| 해야 할 일 | 언제 실제로 일어나나 | 멱등성 보장 |
|---|---|---|
| 마감시각이 지난 조사를 닫고 미응답자 자동 채택 | 그 조사(또는 홈)를 누가 열 때 | `UPDATE ... WHERE status='open'` CAS. rowcount=1인 요청만 자동 채택 실행 |
| 스케줄 요일에 그 주 조사 생성 | 그날 누군가 홈에 처음 접속할 때 | `UNIQUE(schedule_id, survey_date)` + `INSERT OR IGNORE` |

장점: 프로세스가 하나뿐이고, 재시작·시계 문제·중복 실행을 고민할 필요가 없다.
대가: 아무도 접속하지 않으면 아무 일도 일어나지 않는다. 마감 리마인더 같은 push성 기능은 이 구조로는 못 한다(v2 후보).

이 결정은 "회의 직전에 부서원들이 어차피 들어와서 고른다"는 사용 패턴에 기댄 것이다.
사용 패턴이 바뀌면(예: 자동 채택 결과를 마감 즉시 슬랙으로 보내야 한다) 그때 스케줄러를 붙인다.

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
member    → 카페·메뉴 등록/수정, 조사 생성, 응답, 게스트 잔 추가, 스케줄 등록, 내 즐겨찾기
          → 본인이 만든 조사 마감 / 본인이 만든 스케줄 on·off / 본인이 추가한 게스트 잔 삭제
admin     → 위 전부 + 회원 사전 등록·수정, 그룹 트리·멤버 관리, 모든 조사 마감, 모든 게스트 잔 삭제
```

"카페와 메뉴는 누구나 수정"은 의도된 결정이다(위키 모델). 대신 `updated_by/updated_at`을 남긴다.

## 배포 토폴로지

라즈베리파이 1대 + systemd + Tailscale Funnel. 상세 절차는 README의 "운영 배포" 절.
Funnel URL은 기술적으로 공개 URL이므로 실질적 접근 제어는 **구글 로그인 + 회사 도메인 제한 + 사전 등록**이다.

## 하지 않은 것 (의도)

- ORM, 마이그레이션 도구 → 테이블 9개, `CREATE TABLE IF NOT EXISTS`로 충분. 스키마 변경이 생기면 그때 `ALTER` 스크립트를 추가
- JSON API, 프론트 프레임워크 → 폼 POST + 303이 모든 요구를 만족
- 사용자 자기 가입 → 사전 등록이 곧 접근 제어
- 옵션 테이블 정규화 → 메뉴 옵션은 JSON 한 컬럼. 조회 조건으로 쓰지 않으므로 정규화 이득이 없음
