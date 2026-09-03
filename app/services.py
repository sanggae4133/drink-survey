"""도메인 로직: 그룹 트리, 마감·자동 채택, 스케줄 생성, 집계, 텔레그램 알림.

시간 기반 동작(마감, 주간 조사 생성)은 main.py의 1분 주기 tick()과 사용자 접근(lazy) 양쪽에서 실행되며,
멱등성은 SQL 제약(UNIQUE 인덱스, status CAS UPDATE)이 보장하므로 몇 번 겹쳐 돌아도 안전하다.
"""
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

import httpx

from . import config, models
from .db import get_conn, now_min, now_str

log = logging.getLogger(__name__)


KR_WEEKDAYS = "월화수목금토일"


def survey_title(survey) -> str:
    """제목이 없으면 '2026-09-07(월) 시스템1팀'. 화면·주문서 텍스트가 전부 이걸 쓴다."""
    if survey["title"]:
        return survey["title"]
    wd = KR_WEEKDAYS[datetime.strptime(survey["survey_date"], "%Y-%m-%d").weekday()]
    return f"{survey['survey_date']}({wd}) {survey['group_name']}"


# ---------------------------------------------------------------- 그룹 트리

def effective_members(db: sqlite3.Connection, group_id: int) -> list[sqlite3.Row]:
    """유효 멤버 = 직속 멤버 ∪ 모든 하위 그룹 멤버 (중복 제거)."""
    rows = db.execute(
        """
        WITH RECURSIVE g(id) AS (
          SELECT ?
          UNION
          SELECT groups.id FROM groups JOIN g ON groups.parent_group_id = g.id
        )
        SELECT DISTINCT u.id, u.name, u.email, u.status
        FROM g
        JOIN group_members gm ON gm.group_id = g.id
        JOIN users u ON u.id = gm.user_id
        WHERE u.status <> 'disabled'
        ORDER BY u.name
        """,
        (group_id,),
    ).fetchall()
    return rows


def is_effective_member(db: sqlite3.Connection, group_id: int, user_id: int) -> bool:
    return any(m["id"] == user_id for m in effective_members(db, group_id))


def user_visible_group_ids(db: sqlite3.Connection, user_id: int) -> list[int]:
    """홈에 보일 그룹 = 직속 소속 그룹 + 그 조상들(본부 조사도 내 대상이므로)."""
    rows = db.execute(
        """
        WITH RECURSIVE mg(id) AS (
          SELECT group_id FROM group_members WHERE user_id = ?
          UNION
          SELECT g.parent_group_id FROM groups g JOIN mg ON g.id = mg.id
          WHERE g.parent_group_id IS NOT NULL
        )
        SELECT id FROM mg
        """,
        (user_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def creates_cycle(db: sqlite3.Connection, group_id: int, new_parent_id: Optional[int]) -> bool:
    """group_id의 상위를 new_parent_id로 바꿀 때 순환이 생기는지.

    새 상위가 자기 자신이거나 자기 자손이면 순환이다.
    """
    if new_parent_id is None:
        return False
    return db.execute(
        """
        WITH RECURSIVE g(id) AS (
          SELECT ?
          UNION
          SELECT groups.id FROM groups JOIN g ON groups.parent_group_id = g.id
        )
        SELECT 1 FROM g WHERE id = ?
        """,
        (group_id, new_parent_id),
    ).fetchone() is not None


def group_tree(db: sqlite3.Connection) -> list[dict]:
    """트리 순서/깊이가 붙은 평탄화 리스트 (관리 화면 표시용)."""
    rows = db.execute("SELECT * FROM groups ORDER BY name").fetchall()
    children: dict[Optional[int], list[sqlite3.Row]] = {}
    for r in rows:
        children.setdefault(r["parent_group_id"], []).append(r)
    out: list[dict] = []

    def walk(parent_id, depth):
        for g in children.get(parent_id, []):
            out.append({"row": g, "depth": depth})
            walk(g["id"], depth + 1)

    walk(None, 0)
    return out


# ---------------------------------------------------------------- 가격/옵션

def compute_price(menu: sqlite3.Row, selected: list[dict]) -> int:
    return menu["base_price"] + sum(int(s.get("delta_price", 0)) for s in selected)


def default_selection(menu: sqlite3.Row) -> list[dict]:
    """필수 옵션 그룹마다 첫 번째 선택지를 고른 기본 선택."""
    groups = models.parse_option_groups(menu["options"])
    sel = []
    for g in groups:
        if g.required:
            c = g.choices[0]
            sel.append({"name": g.name, "choice": c.label, "delta_price": c.delta_price})
    return sel


# ---------------------------------------------------------------- lazy 마감

def close_survey(db: sqlite3.Connection, survey_id: int, due_only: bool = True) -> None:
    """마감 + 자동 채택. due_only=True면 마감시각이 지난 경우만(lazy), False면 수동 즉시 마감.

    CAS UPDATE라 동시 요청에도 한 번만 실행된다.
    """
    cond = " AND deadline_at <= ?" if due_only else ""
    args = (survey_id, now_min()) if due_only else (survey_id,)
    with db:
        cur = db.execute(
            f"UPDATE surveys SET status='closed' WHERE id=? AND status='open'{cond}", args
        )
        if cur.rowcount:
            _autofill(db, survey_id)
    if cur.rowcount:
        announce(db, survey_id, closed=True)


def _autofill(db: sqlite3.Connection, survey_id: int) -> None:
    """미응답자 자동 채택: ① 그 카페 즐겨찾기 → ② 카페 공통 기본음료 → ③ 제외.

    active 멤버만 대상 — 한 번도 로그인한 적 없는(invited) 사람 몫까지
    시켜버리는 사고를 막는다.
    """
    survey = db.execute("SELECT * FROM surveys WHERE id=?", (survey_id,)).fetchone()
    cafe = db.execute("SELECT * FROM cafes WHERE id=?", (survey["cafe_id"],)).fetchone()
    responded = {
        r["participant_user_id"]
        for r in db.execute(
            "SELECT participant_user_id FROM survey_responses "
            "WHERE survey_id=? AND participant_user_id IS NOT NULL",
            (survey_id,),
        )
    }
    for m in effective_members(db, survey["group_id"]):
        if m["id"] in responded or m["status"] != "active":
            continue
        menu, sel, note = None, [], None
        fav = db.execute(
            "SELECT * FROM user_cafe_defaults WHERE user_id=? AND cafe_id=?",
            (m["id"], survey["cafe_id"]),
        ).fetchone()
        if fav:
            cand = db.execute(
                "SELECT * FROM menus WHERE id=? AND is_active=1", (fav["menu_id"],)
            ).fetchone()
            if cand:
                menu, sel, note = cand, json.loads(fav["selected_options"]), fav["note"]
        if menu is None and cafe["default_menu_id"]:
            cand = db.execute(
                "SELECT * FROM menus WHERE id=? AND is_active=1", (cafe["default_menu_id"],)
            ).fetchone()
            if cand:
                menu, sel = cand, default_selection(cand)
        if menu is None:
            continue  # ③ 제외
        db.execute(
            "INSERT INTO survey_responses "
            "(survey_id, participant_user_id, menu_id, selected_options, final_price, note, is_auto, created_by) "
            "VALUES (?,?,?,?,?,?,1,?)",
            (survey_id, m["id"], menu["id"], json.dumps(sel, ensure_ascii=False),
             compute_price(menu, sel), note, m["id"]),
        )


# ---------------------------------------------------------------- lazy 스케줄 생성

def generate_due_surveys(db: sqlite3.Connection) -> None:
    """오늘이 요일인 스케줄 중 마감 전인 것의 조사를 생성 (멱등: UNIQUE 인덱스 + OR IGNORE).

    마감시각이 이미 지난 채 첫 접속이 오면 그 주는 건너뜀 — 전원 자동 채택된
    '유령 조사'를 만들지 않기 위해서다.
    """
    today = datetime.now()
    weekday = today.weekday()  # 0=월
    today_s = today.strftime("%Y-%m-%d")
    now = now_min()
    scheds = db.execute(
        "SELECT * FROM survey_schedules WHERE enabled=1 AND weekday=?", (weekday,)
    ).fetchall()
    for s in scheds:
        deadline = f"{today_s} {s['deadline_time']}"
        if now >= deadline:
            continue  # stale skip
        title = None
        if s["title_pattern"]:
            md = f"{today.month}/{today.day}({KR_WEEKDAYS[weekday]})"  # 9/4(월)
            title = s["title_pattern"].replace("{M/D}", md)
        with db:
            cur = db.execute(
                "INSERT OR IGNORE INTO surveys "
                "(title, survey_date, group_id, cafe_id, deadline_at, allow_guests, schedule_id, created_by) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (title, today_s, s["group_id"], s["cafe_id"], deadline,
                 s["allow_guests"], s["id"], s["created_by"]),
            )
        if cur.rowcount:
            announce(db, cur.lastrowid, closed=False)


def tick() -> None:
    """1분마다(main.py) 도는 스케줄러 본체: 오늘 스케줄 조사 생성 + 마감 지난 조사 닫기.

    라우터의 lazy 처리와 같은 함수를 쓰므로 스케줄러가 죽어도 사람 접근으로 동작은 이어진다.
    """
    db = get_conn()
    try:
        generate_due_surveys(db)
        due = db.execute(
            "SELECT id FROM surveys WHERE status='open' AND deadline_at <= ?", (now_min(),)
        ).fetchall()
        for r in due:
            close_survey(db, r["id"])
    finally:
        db.close()


# ---------------------------------------------------------------- 텔레그램 알림

def notify_targets(db: sqlite3.Connection, group_id: int) -> list[str]:
    """알림 받을 chat_id 목록.

    ① 그 그룹 → 가장 가까운 상위 순으로 올라가며 chat_id가 있는 첫 그룹 하나.
    ② 위에 아무도 없으면 하위로 내려가며, 각 가지에서 chat_id가 있는 첫 그룹(들).
    → 같은 사람이 두 채팅에서 중복으로 받지 않는다.
    """
    g = db.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    while g is not None:
        if g["telegram_chat_id"]:
            return [g["telegram_chat_id"]]
        g = (db.execute("SELECT * FROM groups WHERE id=?", (g["parent_group_id"],)).fetchone()
             if g["parent_group_id"] else None)
    out: list[str] = []

    def down(gid):
        for c in db.execute("SELECT * FROM groups WHERE parent_group_id=?", (gid,)):
            if c["telegram_chat_id"]:
                out.append(c["telegram_chat_id"])
            else:
                down(c["id"])

    down(group_id)
    return out


def announce(db: sqlite3.Connection, survey_id: int, closed: bool) -> None:
    """조사 생성/마감을 그룹 텔레그램에 알린다. 토큰이 없거나 실패해도 본 동작은 영향 없음."""
    if not config.TELEGRAM_BOT_TOKEN:
        return
    s = build_summary(db, survey_id)
    sv = s["survey"]
    link = f"{config.APP_URL}/surveys/{survey_id}{'/summary' if closed else ''}" if config.APP_URL else ""
    text = (f"✅ 마감\n{s['copy_text']}" if closed
            else f"☕ 새 조사 · {survey_title(sv)}\n{sv['cafe_name']} · 마감 {sv['deadline_at']}")
    for chat_id in notify_targets(db, sv["group_id"]):
        try:  # ponytail: 동기 전송(요청당 최대 5초). 느려지면 asyncio.to_thread/큐로
            httpx.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                       json={"chat_id": chat_id, "text": f"{text}\n{link}".strip()}, timeout=5)
        except httpx.HTTPError as e:
            log.warning("telegram 전송 실패 chat=%s: %s", chat_id, e)


# ---------------------------------------------------------------- 집계(주문서)

def build_summary(db: sqlite3.Connection, survey_id: int) -> dict:
    survey = db.execute(
        "SELECT s.*, g.name AS group_name, c.name AS cafe_name, u.name AS creator_name "
        "FROM surveys s JOIN groups g ON g.id=s.group_id JOIN cafes c ON c.id=s.cafe_id "
        "JOIN users u ON u.id=s.created_by WHERE s.id=?",
        (survey_id,),
    ).fetchone()
    rows = db.execute(
        "SELECT r.*, m.name AS menu_name, pu.name AS participant_name, cu.name AS creator_name "
        "FROM survey_responses r "
        "JOIN menus m ON m.id = r.menu_id "
        "LEFT JOIN users pu ON pu.id = r.participant_user_id "
        "JOIN users cu ON cu.id = r.created_by "
        "WHERE r.survey_id=? ORDER BY r.id",
        (survey_id,),
    ).fetchall()

    combos: dict[tuple, dict] = {}
    persons, guests = [], []
    participant_ids: set[int] = set()
    for r in rows:
        sel = json.loads(r["selected_options"])
        label = models.item_label(r["menu_name"], sel)
        key = (r["menu_id"], json.dumps(sel, sort_keys=True, ensure_ascii=False))
        c = combos.setdefault(key, {"label": label, "qty": 0, "unit_price": r["final_price"], "subtotal": 0})
        c["qty"] += 1
        c["subtotal"] += r["final_price"]
        if r["participant_user_id"] is not None:
            participant_ids.add(r["participant_user_id"])
            persons.append({
                "name": r["participant_name"], "item": label, "note": r["note"],
                "price": r["final_price"], "is_auto": bool(r["is_auto"]),
            })
        else:
            guests.append({
                "id": r["id"], "name": r["guest_label"] or "게스트", "note": r["note"],
                "added_by": r["creator_name"], "item": label, "price": r["final_price"],
                "created_by": r["created_by"],
            })

    persons.sort(key=lambda p: p["name"])
    combo_list = sorted(combos.values(), key=lambda c: (-c["qty"], c["label"]))
    total_qty = sum(c["qty"] for c in combo_list)
    total_price = sum(c["subtotal"] for c in combo_list)

    members = effective_members(db, survey["group_id"])
    excluded = [m["name"] for m in members if m["id"] not in participant_ids]
    auto_count = sum(1 for p in persons if p["is_auto"])

    lines = [f"[{survey_title(survey)}] {survey['cafe_name']}"]
    lines += [f"- {c['label']} x{c['qty']}" for c in combo_list]
    lines += [f"* {p['name']}: {p['note']}" for p in persons + guests if p["note"]]  # 기타 요청
    lines.append(f"합계 {total_qty}잔 {total_price:,}원")

    return {
        "survey": survey, "combos": combo_list, "persons": persons, "guests": guests,
        "total_qty": total_qty, "total_price": total_price,
        "responded_count": len(persons) - auto_count, "auto_count": auto_count,
        "guest_count": len(guests), "excluded": excluded,
        "copy_text": "\n".join(lines),
    }
