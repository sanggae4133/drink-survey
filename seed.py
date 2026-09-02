"""초기 데이터 시드.

사용:
  python seed.py --admin-email you@company.co.kr --admin-name 홍길동          # 관리자만
  python seed.py --admin-email you@company.co.kr --admin-name 홍길동 --demo   # + 예시 데이터
"""
import argparse
import json
from datetime import datetime, timedelta

from app.db import get_conn, init_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-name", default="관리자")
    ap.add_argument("--demo", action="store_true", help="예시 카페/그룹/조사 생성")
    args = ap.parse_args()

    init_db()
    db = get_conn()
    with db:
        db.execute(
            "INSERT INTO users (email, name, role) VALUES (?,?, 'admin') "
            "ON CONFLICT(email) DO UPDATE SET role='admin'",
            (args.admin_email.lower(), args.admin_name),
        )
        admin_id = db.execute("SELECT id FROM users WHERE email=?",
                              (args.admin_email.lower(),)).fetchone()["id"]
        print(f"admin: {args.admin_email} (id={admin_id})")

        if not args.demo:
            return

        # 회원
        member_ids = []
        for i, name in enumerate(["김선임", "박책임", "최선임"], start=1):
            db.execute("INSERT OR IGNORE INTO users (email, name) VALUES (?,?)",
                       (f"demo{i}@example.com", name))
            member_ids.append(db.execute("SELECT id FROM users WHERE email=?",
                                         (f"demo{i}@example.com",)).fetchone()["id"])

        # 카페 + 메뉴
        db.execute("INSERT INTO cafes (name, menu_url, created_by) VALUES (?,?,?)",
                   ("카페 온도", "https://map.naver.com", admin_id))
        cafe_id = db.execute("SELECT id FROM cafes WHERE name='카페 온도'").fetchone()["id"]
        temp_opt = json.dumps([{"name": "온도", "required": True,
                                "choices": [{"label": "HOT", "delta_price": 0}, {"label": "ICE", "delta_price": 0}]},
                               {"name": "샷 추가",
                                "choices": [{"label": "+1샷", "delta_price": 500}]}], ensure_ascii=False)
        for name, price, opts in [("아메리카노", 3500, temp_opt),
                                  ("카페라떼", 4500, temp_opt),
                                  ("유자차", 5000, "[]")]:
            db.execute("INSERT INTO menus (cafe_id, name, base_price, options, created_by) "
                       "VALUES (?,?,?,?,?)", (cafe_id, name, price, opts, admin_id))
        ame_id = db.execute("SELECT id FROM menus WHERE name='아메리카노'").fetchone()["id"]
        db.execute("UPDATE cafes SET default_menu_id=? WHERE id=?", (ame_id, cafe_id))

        # 그룹 트리: 본부 + 팀
        db.execute("INSERT INTO groups (name) VALUES ('코어개발본부')")
        hq = db.execute("SELECT id FROM groups WHERE name='코어개발본부'").fetchone()["id"]
        db.execute("INSERT INTO groups (name, parent_group_id) VALUES ('시스템1팀', ?)", (hq,))
        team = db.execute("SELECT id FROM groups WHERE name='시스템1팀'").fetchone()["id"]
        db.executemany("INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?,?)",
                       [(team, admin_id), *[(team, m) for m in member_ids]])

        # 오늘 조사 하나 (마감 2시간 뒤)
        now = datetime.now()
        deadline = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
        db.execute("INSERT INTO surveys (title, survey_date, group_id, cafe_id, deadline_at, "
                   "allow_guests, created_by) VALUES (?,?,?,?,?,1,?)",
                   (f"{now.month}/{now.day} 데모 조사", now.strftime("%Y-%m-%d"),
                    team, cafe_id, deadline, admin_id))
        print("demo 데이터 생성 완료 (카페 온도 / 코어개발본부·시스템1팀 / 오늘 조사 1건)")


if __name__ == "__main__":
    main()
