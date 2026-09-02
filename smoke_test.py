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
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

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
    dbx = get_conn()
    eff = services.effective_members(dbx, hq)
    dbx.close()
    ok(len(eff) == 5, f"본부 유효 멤버 = 직속 1 + 팀 4 = {len(eff)}")
    admin.post(f"/admin/groups/{hq}/delete")
    admin.post("/admin/groups", data={"name": "빈그룹", "parent_group_id": ""})
    admin.post(f"/admin/groups/{q1("SELECT id FROM groups WHERE name='빈그룹'")['id']}/delete")
    ok(q1("SELECT COUNT(*) n FROM groups")["n"] == 2, "하위 그룹 있는 그룹 삭제 거절(FK), 빈 그룹은 삭제")

    print("== 3. 카페·메뉴 ==")
    admin.post("/cafes", data={"name": "카페 온도", "menu_url": ""})
    cafe = q1("SELECT id FROM cafes WHERE name='카페 온도'")["id"]
    temp_opt = json.dumps([
        {"name": "온도", "required": True,
         "choices": [{"label": "HOT", "delta": 0}, {"label": "ICE", "delta": 0}]},
        {"name": "샷 추가", "choices": [{"label": "+1샷", "delta": 500}]}], ensure_ascii=False)
    admin.post(f"/cafes/{cafe}/menus", data={"name": "아메리카노", "base_price": 3500, "options": temp_opt})
    admin.post(f"/cafes/{cafe}/menus", data={"name": "카페라떼", "base_price": 4500, "options": temp_opt})
    admin.post(f"/cafes/{cafe}/menus", data={"name": "유자차", "base_price": 5000, "options": "[]"})
    r = admin.post(f"/cafes/{cafe}/menus", data={"name": "불량", "base_price": 1, "options": "{bad"})
    ok(q1("SELECT COUNT(*) n FROM menus")["n"] == 3, "옵션 JSON 검증 — 불량 메뉴 거절")
    ame = q1("SELECT id FROM menus WHERE name='아메리카노'")["id"]
    latte = q1("SELECT id FROM menus WHERE name='카페라떼'")["id"]
    admin.post(f"/cafes/{cafe}", data={"name": "카페 온도", "menu_url": "", "default_menu_id": str(ame)})
    ok(q1("SELECT default_menu_id d FROM cafes WHERE id=?", cafe)["d"] == ame, "공통 기본음료 지정")
    admin.post(f"/menus/{latte}", data={"name": "카페라떼", "base_price": 4600,
                                        "options": temp_opt, "is_active": "1"})
    ok(q1("SELECT updated_by u FROM menus WHERE id=?", latte)["u"] == uid["admin@t.co"],
       "메뉴 수정 시 updated_by 기록")

    print("== 4. 조사·응답 (본부 대상 = 트리 전체) ==")
    m1, m2 = client_for("m1@t.co"), client_for("m2@t.co")
    client_for("m3@t.co").get("/")  # m3: active로 만들기 — 즐겨찾기 없음 → 카페 기본음료 대상
    deadline = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
    today = datetime.now().strftime("%Y-%m-%d")
    m1.post("/surveys", data={"survey_date": today, "deadline_time": deadline,
                              "group_id": str(hq), "cafe_id": str(cafe),
                              "title": "스모크 조사", "allow_guests": "1"})
    sid = q1("SELECT id FROM surveys WHERE title='스모크 조사'")["id"]

    m1.post(f"/surveys/{sid}/respond", data={"menu_id": str(ame), "opt_0": "ICE",
                                             "opt_1": "+1샷", "save_default": "1"})
    row = q1("SELECT * FROM survey_responses WHERE survey_id=? AND participant_user_id=?",
             sid, uid["m1@t.co"])
    ok(row and row["final_price"] == 4000, "응답 저장 + 옵션 가격(3500+500)")
    ok(q1("SELECT COUNT(*) n FROM user_cafe_defaults WHERE user_id=?", uid["m1@t.co"])["n"] == 1,
       "즐겨찾기 저장")
    m1.post(f"/surveys/{sid}/respond", data={"menu_id": str(latte), "opt_0": "HOT"})
    rows = q("SELECT * FROM survey_responses WHERE survey_id=? AND participant_user_id=?",
             sid, uid["m1@t.co"])
    ok(len(rows) == 1 and rows[0]["menu_id"] == latte and rows[0]["final_price"] == 4600,
       "1인 1잔 — 재응답은 덮어쓰기(수정된 가격 4600 반영)")

    m2.post(f"/surveys/{sid}/respond", data={"menu_id": str(ame), "opt_0": "ICE", "save_default": "1"})

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

    print("== 6. lazy 스케줄 생성 ==")
    future = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
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
    ok(created[0]["title"] == f"{now.month}/{now.day} 주간회의", "제목 패턴 {M/D} 치환")

    print(f"\nPASS {PASS} checks")
