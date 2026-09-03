"""전체 플로우 스모크 테스트 (구글 OAuth 제외 — dev 로그인 사용).

실행: DEV_LOGIN=1 python smoke_test.py
서버 없이 TestClient로 앱을 직접 구동한다.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "smoke.db")
os.environ["DEV_LOGIN"] = "1"
os.environ["SESSION_SECRET"] = "smoke-test"
os.environ["GOOGLE_CLIENT_ID"] = ""   # .env 값이 들어오지 않게 빈 값으로 고정 (pop이면 config가 .env에서 채움)
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.db import get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402

PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, f"FAIL: {msg}"
    PASS += 1
    print(f"  ok - {msg}")


def q(sql, *args):
    db = get_conn()
    try:
        return db.execute(sql, args).fetchall()
    finally:
        db.close()


def q1(sql, *args):
    rows = q(sql, *args)
    return rows[0] if rows else None


def client_for(email):
    c = TestClient(app)
    r = c.post("/auth/dev", data={"email": email})
    return c


init_db()
db = get_conn()
with db:
    db.execute("INSERT INTO users (email, name, role) VALUES ('admin@t.co','관리자','admin')")
db.close()

with TestClient(app):
    print("== 1. 로그인/사전등록 ==")
    admin = client_for("admin@t.co")
    ok(q1("SELECT status FROM users WHERE email='admin@t.co'")["status"] == "active",
       "dev 로그인 시 invited → active")
    stranger = TestClient(app)
    r = stranger.post("/auth/dev", data={"email": "nobody@t.co"}, follow_redirects=False)
    ok(r.status_code == 303 and stranger.get("/", follow_redirects=False).status_code == 303,
       "미등록 이메일 로그인 거절")

    for i, name in [(1, "김선임"), (2, "박책임"), (3, "최선임"), (4, "정주임")]:
        admin.post("/admin/users", data={"email": f"m{i}@t.co", "name": name, "role": "member"})
    ok(q1("SELECT COUNT(*) n FROM users")["n"] == 5, "회원 사전 등록 4명")
    admin.post("/admin/users", data={"email": "evil@t.co", "name": "x'); alert(1); ('", "role": "member"})
    h = admin.get("/admin/users").text
    ok("confirm('x" not in h and "alert(1); ('" not in h, "회원 이름이 JS 문자열로 새지 않음 (confirm XSS)")
    evil = q1("SELECT id FROM users WHERE email='evil@t.co'")["id"]
    admin.post(f"/admin/users/{evil}/toggle")

    print("== 2. 그룹 트리 ==")
    admin.post("/admin/groups", data={"name": "코어개발본부", "parent_group_id": ""})
    hq = q1("SELECT id FROM groups WHERE name='코어개발본부'")["id"]
    admin.post("/admin/groups", data={"name": "시스템1팀", "parent_group_id": str(hq)})
    team = q1("SELECT id FROM groups WHERE name='시스템1팀'")["id"]
    uid = {r["email"]: r["id"] for r in q("SELECT id, email FROM users")}
    admin.post(f"/admin/groups/{team}/members",
               data={"member_ids": [str(uid[f"m{i}@t.co"]) for i in (1, 2, 3, 4)]})
    admin.post(f"/admin/groups/{hq}/members", data={"member_ids": [str(uid["admin@t.co"])]})
    admin.post(f"/admin/groups/{hq}", data={"name": "코어개발본부", "parent_group_id": str(team)})
    ok(q1("SELECT parent_group_id p FROM groups WHERE id=?", hq)["p"] is None,
       "순환 참조(본부→팀을 상위로) 거절")
    from app import services
    ok(services.survey_title({"title": None, "survey_date": "2026-01-05", "group_name": "시스템1팀"})
       == "2026-01-05(월) 시스템1팀", "제목 없으면 날짜(요일) 그룹명")
    dbx = get_conn()
    eff = services.effective_members(dbx, hq)
    dbx.close()
    ok(len(eff) == 5, f"본부 유효 멤버 = 직속 1 + 팀 4 = {len(eff)}")
    admin.post(f"/admin/groups/{hq}/delete")
    admin.post("/admin/groups", data={"name": "빈그룹", "parent_group_id": ""})
    empty = q1("SELECT id FROM groups WHERE name='빈그룹'")["id"]
    admin.post(f"/admin/groups/{empty}/delete")
    ok(q1("SELECT COUNT(*) n FROM groups")["n"] == 2, "하위 그룹 있는 그룹 삭제 거절(FK), 빈 그룹은 삭제")

    print("== 3. 카페·메뉴 ==")
    admin.post("/cafes", data={"name": "카페 온도", "menu_url": ""})
    cafe = q1("SELECT id FROM cafes WHERE name='카페 온도'")["id"]
    # 옵션 편집 UI 필드: g{i}_name / g{i}_required / g{i}_label[] / g{i}_price[]  (빈 라벨 행은 무시)
    temp_opt = {"g0_name": "온도", "g0_required": "1", "g0_label": ["HOT", "", "ICE"], "g0_price": ["0", "", "0"],
                "g3_name": "샷 추가", "g3_label": ["+1샷"], "g3_price": ["500"]}
    admin.post(f"/cafes/{cafe}/menus", data={"name": "아메리카노", "base_price": 3500, **temp_opt})
    admin.post(f"/cafes/{cafe}/menus", data={"name": "카페라떼", "base_price": 4500, **temp_opt})
    admin.post(f"/cafes/{cafe}/menus", data={"name": "유자차", "base_price": 5000})
    admin.post(f"/cafes/{cafe}/menus", data={"name": "불량", "base_price": 1, "g0_name": "온도"})
    ok(q1("SELECT COUNT(*) n FROM menus")["n"] == 3, "선택지 없는 옵션 그룹 — 메뉴 거절")
    stored = json.loads(q1("SELECT options o FROM menus WHERE name='아메리카노'")["o"])
    ok([g["name"] for g in stored] == ["온도", "샷 추가"] and len(stored[0]["choices"]) == 2
       and stored[1]["choices"][0]["delta_price"] == 500,
       "옵션 UI 폼 → JSON (빈 행 무시, 인덱스 공백 허용, 금액 정수)")
    ame = q1("SELECT id FROM menus WHERE name='아메리카노'")["id"]
    latte = q1("SELECT id FROM menus WHERE name='카페라떼'")["id"]
    admin.post(f"/cafes/{cafe}", data={"name": "카페 온도", "menu_url": "", "default_menu_id": str(ame)})
    ok(q1("SELECT default_menu_id d FROM cafes WHERE id=?", cafe)["d"] == ame, "공통 기본음료 지정")
    admin.post(f"/cafes/{cafe}", data={"name": "카페 온도", "menu_url": "javascript:alert(1)"})
    ok(q1("SELECT menu_url u FROM cafes WHERE id=?", cafe)["u"] is None, "menu_url은 http(s)만 — javascript: 거절")
    big = {f"g{i}_name": f"g{i}" for i in range(21)} | {f"g{i}_label": ["x"] for i in range(21)}
    r = admin.post(f"/cafes/{cafe}/menus", data={"name": "비대", "base_price": 1, **big})
    admin.post(f"/cafes/{cafe}/menus", data={"name": "폭주", "base_price": 1, "g0_name": "x",
                                            "g0_label": ["y"], "g0_price": ["9" * 30]})
    admin.post(f"/cafes/{cafe}/menus", data={"name": "폭주2", "base_price": "9" * 30})
    ok(q1("SELECT COUNT(*) n FROM menus WHERE name LIKE '폭주%'")["n"] == 0, "금액 범위 초과(sqlite 오버플로) 거절")
    exp = admin.get(f"/cafes/{cafe}/export.json")
    ok(exp.status_code == 200 and exp.headers["content-disposition"].startswith("attachment")
       and [m["name"] for m in exp.json()["menus"]] == ["아메리카노", "유자차", "카페라떼"]
       and exp.json()["menus"][0]["options"][0]["name"] == "온도", "JSON 내보내기")
    payload = {"menus": [{"name": "유자차", "base_price": 5500}, {"name": "녹차", "base_price": 4000,
                         "options": [{"name": "온도", "required": True, "choices": [{"label": "HOT"}]}]}]}
    mem = client_for("m2@t.co")
    r = mem.post(f"/cafes/{cafe}/import", files={"file": ("m.json", json.dumps(payload).encode(), "application/json")})
    ok(r.status_code == 403 and q1("SELECT COUNT(*) n FROM menus")["n"] == 3, "가져오기는 관리자만 (member 403)")
    admin.post(f"/cafes/{cafe}/import", files={"file": ("m.json", b"{bad json", "application/json")})
    admin.post(f"/cafes/{cafe}/import", files={"file": ("m.json", json.dumps(
        {"menus": [{"name": "x", "base_price": -1}]}).encode(), "application/json")})
    ok(q1("SELECT COUNT(*) n FROM menus")["n"] == 3, "깨진 파일·범위 밖 값은 전체 거절")
    admin.post(f"/cafes/{cafe}/import", files={"file": ("m.json", json.dumps(payload).encode(), "application/json")})
    ok(q1("SELECT base_price p FROM menus WHERE name='유자차'")["p"] == 5500
       and q1("SELECT COUNT(*) n FROM menus")["n"] == 4, "가져오기: 이름 같으면 갱신, 없으면 추가, 삭제는 없음")
    green = q1("SELECT id FROM menus WHERE name='녹차'")["id"]
    admin.post(f"/menus/{green}/delete")
    admin.post(f"/menus/{ame}/delete")  # 카페 기본음료로 참조 중
    ok(q1("SELECT COUNT(*) n FROM menus WHERE id IN (?,?)", green, ame)["n"] == 1,
       "메뉴 삭제: 참조 없는 건 삭제, 참조(기본음료·응답·즐겨찾기) 있으면 거절")
    ok(q1("SELECT COUNT(*) n FROM menus WHERE name='비대'")["n"] == 0, "옵션 그룹 20개 초과 거절")
    admin.post(f"/menus/{latte}", data={"name": "카페라떼", "base_price": 4600, "is_active": "1", **temp_opt})
    ok(q1("SELECT updated_by u FROM menus WHERE id=?", latte)["u"] == uid["admin@t.co"],
       "메뉴 수정 시 updated_by 기록")

    print("== 4. 조사·응답 (본부 대상 = 트리 전체) ==")
    m1, m2 = client_for("m1@t.co"), client_for("m2@t.co")
    client_for("m3@t.co").get("/")  # m3: active로 만들기 — 즐겨찾기 없음 → 카페 기본음료 대상
    dl = datetime.now() + timedelta(hours=1)  # 자정 넘어가면 survey_date도 같이 넘어가야 한다
    deadline, today = dl.strftime("%H:%M"), dl.strftime("%Y-%m-%d")
    m1.post("/surveys", data={"survey_date": today, "deadline_time": deadline,
                              "group_id": str(hq), "cafe_id": str(cafe),
                              "title": "스모크 조사", "allow_guests": "1"})
    sid = q1("SELECT id FROM surveys WHERE title='스모크 조사'")["id"]
    m1.post("/surveys", data={"survey_date": "zzzz", "deadline_time": "99:99",
                              "group_id": str(hq), "cafe_id": str(cafe), "title": "깨진 날짜"})
    m1.post("/schedules", data={"group_id": str(team), "cafe_id": str(cafe), "weekday": "0", "deadline_time": "abc"})
    ok(q1("SELECT COUNT(*) n FROM surveys WHERE title='깨진 날짜'")["n"] == 0
       and q1("SELECT COUNT(*) n FROM survey_schedules")["n"] == 0
       and m1.get("/").status_code == 200, "형식 깨진 날짜·시각 거절 (홈 500 방지)")
    admin.post("/admin/groups", data={"name": "타부서", "parent_group_id": ""})
    other = q1("SELECT id FROM groups WHERE name='타부서'")["id"]
    m1.post("/surveys", data={"survey_date": today, "deadline_time": deadline,
                              "group_id": str(other), "cafe_id": str(cafe), "title": "남의 부서"})
    m1.post("/schedules", data={"group_id": str(other), "cafe_id": str(cafe), "weekday": "0",
                                "deadline_time": "10:00"})
    ok(q1("SELECT COUNT(*) n FROM surveys WHERE title='남의 부서'")["n"] == 0
       and q1("SELECT COUNT(*) n FROM survey_schedules")["n"] == 0,
       "소속 아닌 그룹에 조사·스케줄 생성 거절")

    m1.post(f"/surveys/{sid}/respond", data={"menu_id": str(ame), "opt_0": "ICE",
                                             "opt_1": "+1샷", "save_default": "1"})
    row = q1("SELECT * FROM survey_responses WHERE survey_id=? AND participant_user_id=?",
             sid, uid["m1@t.co"])
    ok(row and row["final_price"] == 4000, "응답 저장 + 옵션 가격(3500+500)")
    ok(q1("SELECT COUNT(*) n FROM user_cafe_defaults WHERE user_id=?", uid["m1@t.co"])["n"] == 1,
       "즐겨찾기 저장")
    m1.post(f"/surveys/{sid}/respond", data={"menu_id": str(latte), "opt_0": "HOT", "note": "  얼음 적게 "})
    rows = q("SELECT * FROM survey_responses WHERE survey_id=? AND participant_user_id=?",
             sid, uid["m1@t.co"])
    ok(len(rows) == 1 and rows[0]["menu_id"] == latte and rows[0]["final_price"] == 4600,
       "1인 1잔 — 재응답은 덮어쓰기(수정된 가격 4600 반영)")
    ok(rows[0]["note"] == "얼음 적게", "기타 요청 저장(trim)")
    admin.post(f"/menus/{latte}", data={"name": "카페라떼", "base_price": 4700, "is_active": "1", **temp_opt})
    m1.get(f"/surveys/{sid}")
    ok(q1("SELECT final_price p FROM survey_responses WHERE survey_id=? AND participant_user_id=?",
          sid, uid["m1@t.co"])["p"] == 4700, "열린 조사: 메뉴 가격 수정이 응답에 즉시 반영")

    m2.post(f"/surveys/{sid}/respond", data={"menu_id": str(ame), "opt_0": "ICE", "save_default": "1",
                                             "note": "덜 달게"})
    ok(q1("SELECT note FROM user_cafe_defaults WHERE user_id=?", uid["m2@t.co"])["note"] == "덜 달게"
       and "덜 달게" in m2.get("/me").text, "즐겨찾기에 기타 요청도 저장 + 내 설정에 표시")

    m2.post(f"/surveys/{sid}/guests", data={"guest_label": "팀장님 손님", "guest_menu_id": str(latte)})
    g = q1("SELECT * FROM survey_responses WHERE survey_id=? AND participant_user_id IS NULL", sid)
    ok(g and g["created_by"] == uid["m2@t.co"] and g["guest_label"] == "팀장님 손님",
       "게스트 잔 + 추가자 기록")
    m3 = client_for("m3@t.co")
    m3.post(f"/surveys/{sid}/guests/{g['id']}/delete")
    ok(q1("SELECT COUNT(*) n FROM survey_responses WHERE id=?", g["id"])["n"] == 1,
       "추가자·조사 생성자·관리자가 아니면 게스트 잔 삭제 거절")
    m2.post(f"/surveys/{sid}/guests/{g['id']}/delete")
    ok(q1("SELECT COUNT(*) n FROM survey_responses WHERE id=?", g["id"])["n"] == 0,
       "추가한 본인은 게스트 잔 삭제 가능")
    m2.post(f"/surveys/{sid}/guests", data={"guest_label": "손님2", "guest_menu_id": str(ame)})

    print("== 5. lazy 마감 + 자동 채택 ==")
    dbw = get_conn()
    with dbw:
        dbw.execute("UPDATE surveys SET deadline_at=? WHERE id=?",
                    ((datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"), sid))
    dbw.close()
    r = m2.get(f"/surveys/{sid}")  # 접근이 마감을 트리거
    ok(q1("SELECT status s FROM surveys WHERE id=?", sid)["s"] == "closed", "접근 시 lazy 마감")
    auto = q("SELECT * FROM survey_responses WHERE survey_id=? AND is_auto=1", sid)
    auto_by_user = {a["participant_user_id"]: a for a in auto}
    ok(uid["admin@t.co"] in auto_by_user and uid["m3@t.co"] in auto_by_user,
       "미응답 active 멤버 자동 채택 (admin: 카페 기본, m3: 카페 기본)")
    ok(auto_by_user[uid["m3@t.co"]]["final_price"] == 3500, "카페 기본음료(필수옵션 첫값) 가격")
    ok(uid["m4@t.co"] not in auto_by_user, "invited(미로그인) 멤버는 자동 채택 제외")
    r = m2.get(f"/surveys/{sid}/summary")
    ok(r.status_code == 200 and "합계" in r.text and "자동" in r.text, "주문서(집계+개인별) 렌더")
    ok("* 김선임: 얼음 적게" in r.text, "기타 요청이 복사 텍스트에 포함")
    admin.post(f"/menus/{latte}", data={"name": "카페라떼", "base_price": 4800, "is_active": "1", **temp_opt})
    m2.get(f"/surveys/{sid}/summary")
    ok(q1("SELECT final_price p FROM survey_responses WHERE survey_id=? AND participant_user_id=?",
          sid, uid["m1@t.co"])["p"] == 4700, "마감 후: 메뉴 가격이 바뀌어도 주문서 금액 고정")

    print("== 6. lazy 스케줄 생성 ==")
    fut = datetime.now() + timedelta(hours=1)
    future = fut.strftime("%H:%M") if fut.date() == datetime.now().date() else "23:59"  # 자정 직전 방어
    past = (datetime.now() - timedelta(hours=1)).strftime("%H:%M")
    wd = datetime.now().weekday()
    m1.post("/schedules", data={"group_id": str(team), "cafe_id": str(cafe), "weekday": str(wd),
                                "deadline_time": future, "title_pattern": "{M/D} 주간회의"})
    m1.post("/schedules", data={"group_id": str(team), "cafe_id": str(cafe), "weekday": str(wd),
                                "deadline_time": past, "title_pattern": "스테일"})
    m1.get("/")
    m1.get("/")  # 두 번 접근해도 한 번만 생성돼야 함
    created = q("SELECT * FROM surveys WHERE schedule_id IS NOT NULL")
    ok(len(created) == 1, "요일 당일 첫 접속이 조사 생성 (멱등, stale 스케줄은 건너뜀)")
    now = datetime.now()
    ok(created[0]["title"] == f"{now.month}/{now.day}({'월화수목금토일'[now.weekday()]}) 주간회의",
       "제목 패턴 {M/D} → 월/일(요일) 치환")

    print("== 7. 스케줄러 tick + 텔레그램 대상 선택 ==")
    dbw = get_conn()
    with dbw:
        dbw.execute("UPDATE surveys SET deadline_at=? WHERE id=?",
                    ((datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"), created[0]["id"]))
    dbw.close()
    services.tick()  # 사람 접근 없이 스케줄러만으로 마감
    ok(q1("SELECT status s FROM surveys WHERE id=?", created[0]["id"])["s"] == "closed", "tick()이 마감 처리")
    dbx = get_conn()
    with dbx:
        dbx.execute("UPDATE groups SET telegram_chat_id='hq-chat' WHERE id=?", (hq,))
    ok(services.notify_targets(dbx, team) == ["hq-chat"], "팀에 chat_id 없으면 가장 가까운 상위(본부)로")
    with dbx:
        dbx.execute("UPDATE groups SET telegram_chat_id=NULL WHERE id=?", (hq,))
        dbx.execute("UPDATE groups SET telegram_chat_id='team-chat' WHERE id=?", (team,))
    ok(services.notify_targets(dbx, hq) == ["team-chat"], "상위에 없으면 하위로 내려가며 있는 그룹들로")
    with dbx:
        dbx.execute("UPDATE groups SET telegram_chat_id=NULL")
    ok(services.notify_targets(dbx, hq) == [], "아무 데도 없으면 안 보냄")
    dbx.close()

    print("== 8. 접근 회수·하드닝 ==")
    m3 = client_for("m3@t.co")
    admin.post(f"/admin/users/{uid['m3@t.co']}/toggle")
    ok(m3.get("/", follow_redirects=False).status_code == 303, "비활성화된 계정은 기존 세션도 즉시 거절")
    ok(TestClient(app).post("/auth/dev", data={"email": "m3@t.co"}, follow_redirects=False)
       .headers["location"] == "/login" and
       q1("SELECT status s FROM users WHERE email='m3@t.co'")["s"] == "disabled",
       "비활성화된 계정은 재로그인 거절")
    dbx = get_conn()
    ok(all(m["email"] != "m3@t.co" for m in services.effective_members(dbx, hq)),
       "비활성화 계정은 유효 멤버(조사 대상)에서 제외")
    dbx.close()
    admin.post(f"/admin/users/{uid['m3@t.co']}/toggle")
    ok(q1("SELECT status s FROM users WHERE email='m3@t.co'")["s"] == "invited",
       "복구 — 구글 연결 없던 계정은 invited로 (다음 로그인이 다시 active)")
    admin.post(f"/admin/users/{uid['admin@t.co']}", data={"name": "관리자", "email": "admin@t.co", "role": "member"})
    ok(q1("SELECT role r FROM users WHERE email='admin@t.co'")["r"] == "admin", "자기 자신 admin 권한 해제 거절")
    r = admin.post("/cafes", content=b"x" * (65 * 1024),
                   headers={"content-type": "application/x-www-form-urlencoded"})
    ok(r.status_code == 413, "64KB 초과 본문 413")
    r = admin.get("/")
    ok(r.headers.get("x-frame-options") == "DENY" and r.headers.get("x-content-type-options") == "nosniff",
       "보안 헤더")

    print("== 9. 조사·스케줄·그룹 삭제 ==")
    m2.post(f"/surveys/{sid}/delete")
    ok(q1("SELECT COUNT(*) n FROM surveys WHERE id=?", sid)["n"] == 1, "생성자·관리자가 아니면 조사 삭제 거절")
    m1.post(f"/surveys/{sid}/delete")
    ok(q1("SELECT COUNT(*) n FROM surveys WHERE id=?", sid)["n"] == 0
       and q1("SELECT COUNT(*) n FROM survey_responses WHERE survey_id=?", sid)["n"] == 0,
       "생성자가 조사 삭제 → 응답도 함께 삭제")
    sched = q1("SELECT id FROM survey_schedules WHERE title_pattern LIKE '{M/D}%'")["id"]
    m1.post(f"/schedules/{sched}/delete")
    ok(q1("SELECT COUNT(*) n FROM survey_schedules WHERE id=?", sched)["n"] == 0
       and q1("SELECT schedule_id s FROM surveys WHERE id=?", created[0]["id"])["s"] is None,
       "스케줄 삭제 → 만들어진 조사는 남고 schedule_id만 NULL")
    admin.post("/schedules", data={"group_id": str(other), "cafe_id": str(cafe), "weekday": "0", "deadline_time": "10:00"})
    admin.post(f"/admin/groups/{other}/delete")
    ok(q1("SELECT COUNT(*) n FROM groups WHERE id=?", other)["n"] == 0
       and q1("SELECT COUNT(*) n FROM survey_schedules WHERE group_id=?", other)["n"] == 0,
       "그룹 삭제 → 스케줄 CASCADE, 조사 없는 그룹은 삭제됨")
    admin.post(f"/admin/groups/{team}/delete")  # 조사(자동 생성된 주간회의)가 남아 있음
    ok(q1("SELECT COUNT(*) n FROM groups WHERE id=?", team)["n"] == 1, "조사(기록)가 남은 그룹은 삭제 거절")

    print(f"\nPASS {PASS} checks")
