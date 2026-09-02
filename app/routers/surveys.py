"""조사: 생성/상세/응답/게스트/마감/복제/주문서."""
import json
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import models, services
from ..db import db_dep, now_min, now_str
from ..deps import current_user, flash, render

router = APIRouter(prefix="/surveys", tags=["surveys"])


def _get_survey(db, sid: int) -> sqlite3.Row:
    s = db.execute(
        "SELECT s.*, g.name AS group_name, c.name AS cafe_name "
        "FROM surveys s JOIN groups g ON g.id=s.group_id JOIN cafes c ON c.id=s.cafe_id "
        "WHERE s.id=?", (sid,)).fetchone()
    if s is None:
        raise HTTPException(404, "조사를 찾을 수 없습니다")
    return s


def _can_manage(user, survey) -> bool:
    return user["role"] == "admin" or user["id"] == survey["created_by"]


def _parse_selection(menu: sqlite3.Row, form) -> list[dict]:
    """폼의 opt_{i} 필드를 메뉴 옵션 정의에 대조해 선택 스냅샷 생성."""
    groups = models.parse_option_groups(menu["options"])
    sel = []
    for i, g in enumerate(groups):
        v = (form.get(f"opt_{i}") or "").strip()
        if not v:
            if g.required:
                raise ValueError(f"'{g.name}' 옵션을 선택하세요")
            continue
        choice = next((c for c in g.choices if c.label == v), None)
        if choice is None:
            raise ValueError(f"'{g.name}'에 없는 선택지입니다")
        sel.append({"name": g.name, "choice": choice.label, "delta": choice.delta})
    return sel


# ------------------------------------------------------------------ 생성/복제

@router.get("/new")
def new_survey(request: Request, user: sqlite3.Row = Depends(current_user),
               db: sqlite3.Connection = Depends(db_dep)):
    src_id = request.query_params.get("from")
    src = db.execute("SELECT * FROM surveys WHERE id=?", (src_id,)).fetchone() if src_id else None
    groups = db.execute("SELECT * FROM groups ORDER BY name").fetchall()
    cafes = db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM menus m WHERE m.cafe_id=c.id AND m.is_active=1) AS menu_count "
        "FROM cafes c WHERE c.is_active=1 ORDER BY c.name").fetchall()
    return render(request, "survey_new.html", user=user, groups=groups, cafes=cafes,
                  src=src, today=now_min()[:10])


@router.post("")
def create_survey(request: Request,
                  survey_date: str = Form(...), deadline_time: str = Form(...),
                  group_id: int = Form(...), cafe_id: int = Form(...),
                  title: str = Form(""), allow_guests: str = Form(""),
                  user: sqlite3.Row = Depends(current_user),
                  db: sqlite3.Connection = Depends(db_dep)):
    menu_count = db.execute(
        "SELECT COUNT(*) AS n FROM menus WHERE cafe_id=? AND is_active=1", (cafe_id,)
    ).fetchone()["n"]
    if not menu_count:
        flash(request, "그 카페에는 아직 메뉴가 없습니다. 메뉴를 먼저 등록하세요")
        return RedirectResponse("/surveys/new", status_code=303)
    deadline_at = f"{survey_date} {deadline_time}"
    if deadline_at <= now_min():
        flash(request, "마감 시각이 이미 지났습니다")
        return RedirectResponse("/surveys/new", status_code=303)
    with db:
        cur = db.execute(
            "INSERT INTO surveys (title, survey_date, group_id, cafe_id, deadline_at, allow_guests, created_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (title.strip() or None, survey_date, group_id, cafe_id, deadline_at,
             1 if allow_guests else 0, user["id"]))
        sid = cur.lastrowid
    return RedirectResponse(f"/surveys/{sid}", status_code=303)


# ------------------------------------------------------------------ 상세/응답

@router.get("/{sid}")
def survey_detail(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                  db: sqlite3.Connection = Depends(db_dep)):
    services.close_survey(db, sid)
    survey = _get_survey(db, sid)
    if survey["status"] == "closed":
        return RedirectResponse(f"/surveys/{sid}/summary", status_code=303)

    fav = db.execute(
        "SELECT d.*, m.name AS menu_name, m.is_active AS menu_active "
        "FROM user_cafe_defaults d JOIN menus m ON m.id=d.menu_id "
        "WHERE d.user_id=? AND d.cafe_id=?", (user["id"], survey["cafe_id"])).fetchone()
    menu_rows = db.execute(
        "SELECT * FROM menus WHERE cafe_id=? AND is_active=1 ORDER BY name",
        (survey["cafe_id"],)).fetchall()
    menus = [{"row": m, "groups": models.parse_option_groups(m["options"])} for m in menu_rows]
    if fav:  # 즐겨찾기 메뉴를 맨 위로
        menus.sort(key=lambda m: 0 if m["row"]["id"] == fav["menu_id"] else 1)

    my_response = db.execute(
        "SELECT r.*, m.name AS menu_name FROM survey_responses r JOIN menus m ON m.id=r.menu_id "
        "WHERE r.survey_id=? AND r.participant_user_id=?", (sid, user["id"])).fetchone()
    summary = services.build_summary(db, sid)  # 진행 중 현황 표시에 재사용
    is_member = services.is_effective_member(db, survey["group_id"], user["id"])

    return render(request, "survey_detail.html", user=user, survey=survey, menus=menus,
                  fav=fav, my_response=my_response, summary=summary,
                  item_label=models.item_label, json_loads=json.loads,
                  is_member=is_member, can_manage=_can_manage(user, survey))


@router.post("/{sid}/respond")
async def respond(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                  db: sqlite3.Connection = Depends(db_dep)):
    services.close_survey(db, sid)
    survey = _get_survey(db, sid)
    if survey["status"] != "open":
        flash(request, "이미 마감된 조사입니다")
        return RedirectResponse(f"/surveys/{sid}/summary", status_code=303)
    if not services.is_effective_member(db, survey["group_id"], user["id"]):
        flash(request, "이 조사의 대상 그룹 멤버가 아닙니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)

    form = await request.form()
    try:
        if form.get("use_default"):  # 즐겨찾기 원터치
            fav = db.execute(
                "SELECT * FROM user_cafe_defaults WHERE user_id=? AND cafe_id=?",
                (user["id"], survey["cafe_id"])).fetchone()
            if fav is None:
                raise ValueError("이 카페의 즐겨찾기가 없습니다")
            menu = db.execute("SELECT * FROM menus WHERE id=? AND is_active=1",
                              (fav["menu_id"],)).fetchone()
            if menu is None:
                raise ValueError("즐겨찾기 메뉴가 판매 중이 아닙니다. 다시 선택해 주세요")
            sel = json.loads(fav["selected_options"])
        else:
            menu = db.execute("SELECT * FROM menus WHERE id=? AND cafe_id=? AND is_active=1",
                              (int(form["menu_id"]), survey["cafe_id"])).fetchone()
            if menu is None:
                raise ValueError("메뉴를 찾을 수 없습니다")
            sel = _parse_selection(menu, form)
    except (ValueError, KeyError) as e:
        flash(request, str(e) or "입력이 잘못됐습니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)

    price = services.compute_price(menu, sel)
    sel_json = json.dumps(sel, ensure_ascii=False)
    with db:
        cur = db.execute(
            "UPDATE survey_responses SET menu_id=?, selected_options=?, final_price=?, is_auto=0, updated_at=? "
            "WHERE survey_id=? AND participant_user_id=?",
            (menu["id"], sel_json, price, now_str(), sid, user["id"]))
        if cur.rowcount == 0:
            db.execute(
                "INSERT INTO survey_responses "
                "(survey_id, participant_user_id, menu_id, selected_options, final_price, created_by) "
                "VALUES (?,?,?,?,?,?)",
                (sid, user["id"], menu["id"], sel_json, price, user["id"]))
        if form.get("save_default"):
            db.execute(
                "INSERT INTO user_cafe_defaults (user_id, cafe_id, menu_id, selected_options) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(user_id, cafe_id) DO UPDATE SET menu_id=excluded.menu_id, "
                "selected_options=excluded.selected_options",
                (user["id"], survey["cafe_id"], menu["id"], sel_json))
    flash(request, f"저장됨: {models.item_label(menu['name'], sel)} {price:,}원")
    return RedirectResponse(f"/surveys/{sid}", status_code=303)


@router.post("/{sid}/guests")
async def add_guest(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                    db: sqlite3.Connection = Depends(db_dep)):
    services.close_survey(db, sid)
    survey = _get_survey(db, sid)
    if survey["status"] != "open" or not survey["allow_guests"]:
        flash(request, "게스트 잔을 추가할 수 없는 조사입니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)
    if not services.is_effective_member(db, survey["group_id"], user["id"]):
        flash(request, "이 조사의 대상 그룹 멤버가 아닙니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)

    form = await request.form()
    try:
        menu = db.execute("SELECT * FROM menus WHERE id=? AND cafe_id=? AND is_active=1",
                          (int(form["guest_menu_id"]), survey["cafe_id"])).fetchone()
        if menu is None:
            raise ValueError("메뉴를 찾을 수 없습니다")
        sel = services.default_selection(menu)  # 게스트 잔은 필수 옵션 기본값으로
    except (ValueError, KeyError) as e:
        flash(request, str(e) or "입력이 잘못됐습니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)

    label = (form.get("guest_label") or "").strip() or "게스트"
    with db:
        db.execute(
            "INSERT INTO survey_responses "
            "(survey_id, guest_label, menu_id, selected_options, final_price, created_by) "
            "VALUES (?,?,?,?,?,?)",
            (sid, label, menu["id"], json.dumps(sel, ensure_ascii=False),
             services.compute_price(menu, sel), user["id"]))
    flash(request, f"게스트 잔 추가됨: {label}")
    return RedirectResponse(f"/surveys/{sid}", status_code=303)


@router.post("/{sid}/guests/{rid}/delete")
def delete_guest(sid: int, rid: int, request: Request,
                 user: sqlite3.Row = Depends(current_user),
                 db: sqlite3.Connection = Depends(db_dep)):
    survey = _get_survey(db, sid)
    row = db.execute(
        "SELECT * FROM survey_responses WHERE id=? AND survey_id=? AND participant_user_id IS NULL",
        (rid, sid)).fetchone()
    if survey["status"] != "open" or row is None:
        flash(request, "삭제할 수 없습니다")
    elif user["id"] not in (row["created_by"], survey["created_by"]) and user["role"] != "admin":
        flash(request, "본인이 추가한 게스트 잔만 삭제할 수 있습니다")
    else:
        with db:
            db.execute("DELETE FROM survey_responses WHERE id=?", (rid,))
        flash(request, "게스트 잔을 삭제했습니다")
    return RedirectResponse(f"/surveys/{sid}", status_code=303)


# ------------------------------------------------------------------ 마감/주문서

@router.post("/{sid}/close")
def close_survey(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                 db: sqlite3.Connection = Depends(db_dep)):
    survey = _get_survey(db, sid)
    if not _can_manage(user, survey):
        flash(request, "조사 생성자나 관리자만 마감할 수 있습니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)
    services.close_survey(db, sid, due_only=False)
    return RedirectResponse(f"/surveys/{sid}/summary", status_code=303)


@router.get("/{sid}/summary")
def survey_summary(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                   db: sqlite3.Connection = Depends(db_dep)):
    services.close_survey(db, sid)
    survey = _get_survey(db, sid)
    summary = services.build_summary(db, sid)
    return render(request, "survey_summary.html", user=user, survey=survey, s=summary,
                  interim=(survey["status"] == "open"))
