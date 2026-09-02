# 구현 안내

> "이걸 고치려면 어디를 열어야 하나"에 답하는 문서. 파일 → 함수 → 라우트 → 템플릿 순.
> 규칙의 이유는 [design.md](design.md), 전체 그림은 [architecture.md](architecture.md).

## 파일 지도

```
app/
  main.py          앱 조립. SessionMiddleware, 본문 64KB 상한·보안 헤더 미들웨어, 라우터 7개, import 시점에 init_db()
  config.py        환경변수 → 상수. OAUTH_CONFIGURED = client id/secret 둘 다 있을 때
  db.py            get_conn() / init_db() / db_dep() / now_str() / now_min()
  schema.sql       DDL 원본. CREATE ... IF NOT EXISTS 라 매 기동마다 실행해도 안전
  models.py        OptionGroup·OptionChoice(pydantic), parse_option_groups(), item_label()
  services.py      도메인 로직 (아래 표)
  deps.py          current_user / require_admin / flash / render
  routers/
    auth.py        /login, /auth/google, /auth/callback, /auth/dev, /logout
    home.py        /            lazy 스케줄 생성·마감이 걸리는 진입점
    cafes.py       /cafes, /cafes/{id}, /cafes/{id}/menus, /menus/{id}
    surveys.py     /surveys/*   생성·상세·응답·게스트·마감·주문서
    schedules.py   /schedules, /schedules/{id}/toggle
    me.py          /me, /me/defaults/{cafe_id}/delete
    admin.py       /admin/users*, /admin/groups*
  templates/       base.html + 화면당 1개. CSS는 base.html 안 <style> 한 덩어리
seed.py            관리자 upsert (+ --demo 예시 데이터)
smoke_test.py      TestClient로 전체 플로우 22 체크. 서버 불필요
run.sh             .env 로드 후 uvicorn
```

## services.py 함수 표

| 함수 | 하는 일 | 호출처 |
|---|---|---|
| `effective_members(db, gid)` | 재귀 CTE로 유효 멤버 rows (id, name, email, status) | 자동 채택, 주문서 제외 목록, 관리 화면 인원수 |
| `is_effective_member(db, gid, uid)` | 위 결과에 uid가 있는지 | 응답·게스트 추가 권한, 상세 화면 |
| `user_visible_group_ids(db, uid)` | 직속 소속 그룹 + 조상 id 목록 (재귀 CTE, 위로) | 홈 |
| `creates_cycle(db, gid, new_parent)` | new_parent가 gid 자신·자손이면 True | 그룹 상위 변경 |
| `group_tree(db)` | `[{row, depth}]` 트리 순서 평탄화 | 그룹 관리 화면 |
| `compute_price(menu, sel)` | `base_price + Σ delta_price` | 응답, 게스트, 자동 채택 |
| `default_selection(menu)` | 필수 옵션 그룹마다 첫 선택지 | 게스트 잔, 카페 기본음료 자동 채택 |
| `close_survey(db, sid, due_only=True)` | CAS로 closed 전환, 성공 시 `_autofill`. `due_only=False`면 수동 즉시 마감 | 홈·상세·응답·게스트·주문서 진입, 수동 마감 |
| `_autofill(db, sid)` | 미응답 active 멤버에 즐겨찾기 → 기본음료 → 제외 순으로 `is_auto=1` 응답 INSERT | `close_survey` 안에서만 |
| `generate_due_surveys(db)` | 오늘 요일 스케줄 → stale skip → `INSERT OR IGNORE` | 홈 |
| `build_summary(db, sid)` | 주문서 dict (combos, persons, guests, 합계, excluded, copy_text) | 상세 화면 현황, 주문서 화면 |
| `survey_title(row)` | 제목 없으면 `날짜(요일) 그룹명`. Jinja 전역으로도 등록(deps.py) | 홈·상세·주문서 템플릿, copy_text |

`_autofill`은 `close_survey`의 `with db:` 트랜잭션 안에서 실행된다. 자동 채택 중 예외가 나면 마감도 롤백된다(다음 접근이 다시 시도).

## 라우트 표

| 메서드·경로 | 권한 | 핵심 동작 | 성공 후 |
|---|---|---|---|
| GET `/login` | - | 로그인 화면. 이미 로그인이면 `/` | |
| GET `/auth/google` → `/auth/callback` | - | authlib. R1 검증 순서대로 바인딩 | `/` |
| POST `/auth/dev` | DEV_LOGIN=1 | 등록 이메일로 즉시 로그인. invited→active | `/` |
| POST `/logout` | 로그인 | 세션 clear | `/login` |
| GET `/` | 로그인 | `generate_due_surveys` → 내 그룹의 due 조사 `close_survey` → 목록 | |
| GET `/surveys/new?from=ID` | 로그인 | 생성 폼. `from`이 있으면 그 조사 값 프리필("같게 만들기") | |
| POST `/surveys` | 대상 그룹 유효 멤버 또는 admin | 메뉴 있는 카페인지, 마감이 미래인지 검사 후 INSERT | `/surveys/{id}` |
| GET `/surveys/{id}` | 로그인 | lazy 마감 → closed면 `/summary`로 303. 아니면 메뉴·옵션 폼·현황 | |
| POST `/surveys/{id}/respond` | 유효 멤버 | `use_default`면 즐겨찾기로, 아니면 `menu_id` + `opt_{i}` 파싱. UPDATE→없으면 INSERT. `save_default`면 즐겨찾기 upsert | `/surveys/{id}` |
| POST `/surveys/{id}/guests` | 유효 멤버, allow_guests | `guest_menu_id` + 필수 옵션 기본값으로 INSERT | `/surveys/{id}` |
| POST `/surveys/{id}/guests/{rid}/delete` | 추가자·생성자·admin, open | DELETE | `/surveys/{id}` |
| POST `/surveys/{id}/close` | 생성자·admin | `close_survey(due_only=False)` | `/summary` |
| GET `/surveys/{id}/summary` | 로그인 | lazy 마감 → `build_summary`. open이면 "진행 중" 배지 | |
| GET/POST `/cafes` | 로그인 | 목록 / 등록. `menu_url`은 http(s)만 | `/cafes/{id}` |
| GET/POST `/cafes/{id}` | 로그인 | 상세·메뉴 목록 / 이름·링크·기본음료 수정 | |
| POST `/cafes/{id}/menus` | 로그인 | 옵션 UI 필드 → JSON 조립·검증 후 INSERT | `/cafes/{id}` |
| POST `/menus/{id}` | 로그인 | 같은 조립·검증 후 UPDATE + updated_by/at | `/cafes/{cafe}` |
| GET/POST `/schedules` | 로그인 / 대상 그룹 유효 멤버 또는 admin | 목록 / 등록 | |
| POST `/schedules/{id}/toggle` | 생성자·admin | enabled 반전 | |
| GET `/me` | 로그인 | 내 즐겨찾기 목록 | |
| POST `/me/defaults/{cafe_id}/delete` | 본인 | DELETE | `/me` |
| GET/POST `/admin/users` | admin | 목록 / 사전 등록 | |
| POST `/admin/users/{id}` | admin | 이름·역할 수정. invited가 아니면 이메일 변경 거절. 자기 admin 해제 거절 | |
| POST `/admin/users/{id}/toggle` | admin | disabled ↔ active/invited. 자기 자신 불가 | |
| GET/POST `/admin/groups` | admin | 트리 + 멤버 체크박스 / 그룹 생성 | |
| POST `/admin/groups/{id}` | admin | 이름·상위 변경. `creates_cycle`이면 거절 | |
| POST `/admin/groups/{id}/members` | admin | 직속 멤버 전체 교체 (DELETE 후 INSERT) | |
| POST `/admin/groups/{id}/delete` | admin | DELETE. FK 위반(`IntegrityError`)이면 flash로 거절 | |

권한 실패 처리 방식이 두 가지다. **접근 자체가 잘못된 것**(비로그인, 비관리자, 없는 id)은 303/403/404,
**규칙 위반**(멤버 아님, 마감됨, 순환)은 flash 메시지 + 원래 화면으로 303. 사용자가 고칠 수 있는 건 후자로 처리한다.

## 폼 규약

| 필드 | 형식 | 처리 |
|---|---|---|
| 체크박스 (`allow_guests`, `is_active`, `save_default`, `use_default`) | 체크 시 `"1"`, 아니면 필드 없음 | `str = Form("")` 후 truthy 검사 |
| 선택 없음을 허용하는 select (`parent_group_id`, `default_menu_id`) | `""` = 없음 | `str = Form("")` 후 `int(v) if v else None` |
| 옵션 라디오 | `opt_{i}` (i = 옵션 그룹 인덱스), 값은 `choices[].label`, 비필수는 `""`이 "없음" | `_parse_selection`이 메뉴 정의와 대조. 정의에 없는 값은 거절 |
| 기타 요청 (`note`) | 자유 텍스트, 100자 | 응답·게스트 잔에 저장. 집계 합산엔 안 섞고 개인별·복사 텍스트에 `* 이름: 요청` |
| 옵션 편집 UI (메뉴 추가/수정) | `g{i}_name`, `g{i}_required`, `g{i}_label[]`, `g{i}_price[]` | `cafes._options_from_form`이 JSON으로 조립 → `parse_option_groups` 검증. 빈 라벨 행 무시, 인덱스 공백 허용(그룹 삭제) |
| 다중 체크박스 (`member_ids`) | 같은 이름 여러 개 | `await request.form()` → `getlist` |

옵션 라디오가 인덱스 기반인 이유: 옵션 그룹 이름에 어떤 문자가 와도 폼 이름이 깨지지 않게. 대조는 서버가 메뉴 JSON을 다시 파싱해서 한다.

## 템플릿

- `base.html` — nav(로그인 상태면), flash 한 줄, `{% block content %}`. CSS 전부 여기.
  컨텍스트에 `user`가 없으면(로그인 화면) nav를 그리지 않는다.
- `render(request, name, **ctx)`가 `flash`를 세션에서 pop해 넣는다. 라우터에서 `user=user`는 직접 넘겨야 한다.
- `survey_detail.html`은 `item_label`, `json_loads`를 컨텍스트로 받아 템플릿 안에서 호출한다. 필터 등록 대신 함수 주입.
- `cafe_detail.html`의 옵션 편집기는 Jinja 매크로(`opt_group`, `opt_row`)로 그리고, 같은 매크로를 `i='{i}'`로 렌더한 `<template>`을
  JS가 `replaceAll('{i}', 인덱스)`로 복제한다. 마크업이 한 곳에만 있다. 새 그룹 인덱스는 기존 최댓값+1.
- 테이블은 `.tbl-scroll`로 감싸 모바일에서 가로 스크롤.
- 숫자는 `'{:,}'.format(n)` 으로 천 단위 콤마, `.num` 클래스로 tabular-nums.

## 설정

| 변수 | 기본 | 의미 |
|---|---|---|
| `DB_PATH` | `<repo>/drink_survey.db` | SQLite 파일 |
| `SESSION_SECRET` | `dev-secret-change-me` | 세션 쿠키 서명. 운영은 반드시 교체 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | 빈 값 | 둘 다 있어야 구글 로그인 버튼 활성 |
| `ALLOWED_DOMAIN` | 빈 값 | 설정 시 이 도메인 이메일만 허용 + 구글 `hd` 힌트 |
| `DEV_LOGIN` | `0` | `1`이면 dev 로그인 폼 노출 + `/docs` 활성 + 세션 쿠키 `Secure`·HSTS 해제. `0`이면 기본 시크릿·OAuth 미설정 시 기동 거부 |
| `PORT` | `8080` | run.sh만 사용 |

`config.py`는 import 시점에 환경변수를 읽는다. 테스트가 `os.environ`을 먼저 세팅한 뒤 `app.main`을 import하는 이유.

## 테스트

```bash
DEV_LOGIN=1 python smoke_test.py
```

- 임시 디렉터리에 새 DB를 만들고 `TestClient`로 전 플로우를 순서대로 밟는다. 40 체크, 약 1초.
- 단위 테스트 프레임워크 없음. `ok(cond, msg)` 하나. 실패하면 첫 실패에서 `AssertionError`로 멈춘다.
- 시간 의존 테스트(마감, 스케줄)는 DB의 `deadline_at`을 직접 과거로 UPDATE해서 트리거한다.
- 구글 OAuth 콜백은 테스트 안 함. dev 로그인이 같은 `invited→active` 경로를 탄다.

새 규칙을 넣으면 여기 `ok()` 한 줄을 같이 넣는다. 그게 이 프로젝트의 테스트 정책 전부다.

## 자주 하는 변경, 어디를 고치나

| 하려는 것 | 고칠 곳 |
|---|---|
| 컬럼 추가 | `schema.sql`에 `CREATE TABLE`(신규 설치용) + 기존 DB용 `ALTER TABLE` 스크립트를 별도로. `IF NOT EXISTS`는 컬럼 추가를 못 한다 |
| 자동 채택 우선순위 변경 | `services._autofill` |
| 홈 정렬·노출 조건 | `routers/home.py` SQL. 현재: 진행 중은 그룹명 → 날짜, 지난 건 최신 10개 |
| 주문서 텍스트 포맷 | `services.build_summary`의 `lines` |
| 게스트 잔에 옵션 선택 추가 | `survey_detail.html` 게스트 폼에 라디오 추가 + `add_guest`에서 `default_selection` 대신 `_parse_selection` 사용 |
| 옵션 편집기에 항목 추가(예: 선택지 설명) | `cafe_detail.html`의 `opt_row` 매크로 + `cafes._options_from_form` + `models.OptionChoice` 세 곳 |
| 새 권한 규칙 | 라우터 핸들러 상단. `_can_manage` 패턴 참고 |
| 스케줄러 도입(리마인더 등) | `close_survey`/`generate_due_surveys`는 이미 순수 함수라 그대로 호출 가능. 트리거만 추가 |
| 시간대 지원 | 전 컬럼이 로컬 문자열이라 큰 변경. 그 전에 정말 필요한지부터 |

## 알려진 한계

- `is_effective_member`는 멤버 전체를 가져와 검사한다. 그룹이 수백 명이 되면 `WHERE u.id=?` 쿼리로 바꿀 것.
- 동시에 두 요청이 같은 사람의 첫 응답을 넣으면 한쪽이 UNIQUE 위반으로 500. `INSERT ... ON CONFLICT DO UPDATE`로 바꾸면 해결.
- 스키마 마이그레이션 도구 없음. 첫 `ALTER`가 필요해질 때 `migrations/` 디렉터리와 버전 테이블을 그때 만든다.
- `main.py`가 import 시점에 `init_db()`를 호출한다. `DB_PATH`를 바꿔 테스트하려면 import 전에 환경변수를 세팅해야 한다.
