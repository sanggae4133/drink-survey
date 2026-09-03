"""조사: 생성/상세/응답/게스트/마감/복제/주문서."""
import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import config, models, services
from datetime import datetime, timedelta

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


def _note(form) -> Optional[str]:
    """기타 요청(서술형). 100자로 자른다 — 주문서에 한 줄로 들어가야 하니."""
    return (form.get("note") or "").strip()[:100] or None


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
        sel.append({"name": g.name, "choice": choice.label, "delta_price": choice.delta_price})
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
    default = datetime.now() + timedelta(hours=1)  # 마감 기본값: 지금부터 1시간 뒤 (자정 넘으면 날짜도 내일)
    return render(request, "survey_new.html", user=user, groups=groups, cafes=cafes, src=src,
                  today=default.strftime("%Y-%m-%d"), default_time=default.strftime("%H:%M"),
                  default_remind=",".join(map(str, sorted(config.REMIND_MINUTES, reverse=True))) or "없음")


@router.post("")
def create_survey(request: Request,
                  survey_date: str = Form(...), deadline_time: str = Form(...),
                  group_id: int = Form(...), cafe_id: int = Form(...),
                  title: str = Form(""), allow_guests: str = Form(""), remind_minutes: str = Form(""),
                  user: sqlite3.Row = Depends(current_user),
                  db: sqlite3.Connection = Depends(db_dep)):
    try:
        remind = services.remind_minutes_from_form(remind_minutes)
    except ValueError as e:
        flash(request, str(e))
        return RedirectResponse("/surveys/new", status_code=303)
    menu_count = db.execute(
        "SELECT COUNT(*) AS n FROM menus WHERE cafe_id=? AND is_active=1", (cafe_id,)
    ).fetchone()["n"]
    if not menu_count:
        flash(request, "그 카페에는 아직 메뉴가 없습니다. 메뉴를 먼저 등록하세요")
        return RedirectResponse("/surveys/new", status_code=303)
    if user["role"] != "admin" and not services.is_effective_member(db, group_id, user["id"]):
        flash(request, "자기가 속한 그룹(또는 그 상위 그룹)에만 조사를 열 수 있습니다")
        return RedirectResponse("/surveys/new", status_code=303)
    deadline_at = f"{survey_date} {deadline_time}"
    try:
        datetime.strptime(deadline_at, "%Y-%m-%d %H:%M")  # 형식이 깨지면 문자열 비교·요일 계산이 전부 틀어진다
    except ValueError:
        flash(request, "날짜·시각 형식이 잘못됐습니다")
        return RedirectResponse("/surveys/new", status_code=303)
    if deadline_at <= now_min():
        flash(request, "마감 시각이 이미 지났습니다")
        return RedirectResponse("/surveys/new", status_code=303)
    with db:
        cur = db.execute(
            "INSERT INTO surveys (title, survey_date, group_id, cafe_id, deadline_at, allow_guests, remind_minutes, created_by) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title.strip() or None, survey_date, group_id, cafe_id, deadline_at,
             1 if allow_guests else 0, remind, user["id"]))
        sid = cur.lastrowid
    services.announce(db, sid, "created")
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

    with db:
        services.refresh_prices(db, sid)  # 열린 조사: 현재 메뉴 가격 반영
    my_response = db.execute(
        "SELECT r.*, m.name AS menu_name FROM survey_responses r JOIN menus m ON m.id=r.menu_id "
        "WHERE r.survey_id=? AND r.participant_user_id=?", (sid, user["id"])).fetchone()
    summary = services.build_summary(db, sid)  # 진행 중 현황 표시에 재사용
    is_member = services.is_effective_member(db, survey["group_id"], user["id"])

    remind = sorted(services.effective_remind_minutes(db, survey), reverse=True)
    return render(request, "survey_detail.html", user=user, survey=survey, menus=menus,
                  fav=fav, my_response=my_response, summary=summary,
                  item_label=models.item_label, json_loads=json.loads,
                  is_member=is_member, can_manage=_can_manage(user, survey),
                  remind_text="·".join(map(str, remind)) + "분 전" if remind else "없음",
                  telegram_on=bool(config.TELEGRAM_BOT_TOKEN))


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
    note = _note(form)
    with db:
        cur = db.execute(
            "UPDATE survey_responses SET menu_id=?, selected_options=?, final_price=?, note=?, is_auto=0, updated_at=? "
            "WHERE survey_id=? AND participant_user_id=?",
            (menu["id"], sel_json, price, note, now_str(), sid, user["id"]))
        if cur.rowcount == 0:
            db.execute(
                "INSERT INTO survey_responses "
                "(survey_id, participant_user_id, menu_id, selected_options, final_price, note, created_by) "
                "VALUES (?,?,?,?,?,?,?)",
                (sid, user["id"], menu["id"], sel_json, price, note, user["id"]))
        if form.get("save_default"):
            db.execute(
                "INSERT INTO user_cafe_defaults (user_id, cafe_id, menu_id, selected_options, note) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(user_id, cafe_id) DO UPDATE SET menu_id=excluded.menu_id, "
                "selected_options=excluded.selected_options, note=excluded.note",
                (user["id"], survey["cafe_id"], menu["id"], sel_json, note))
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

    label = (form.get("guest_label") or "").strip()[:40] or "게스트"
    with db:
        db.execute(
            "INSERT INTO survey_responses "
            "(survey_id, guest_label, menu_id, selected_options, final_price, note, created_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, label, menu["id"], json.dumps(sel, ensure_ascii=False),
             services.compute_price(menu, sel), _note(form), user["id"]))
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


# ------------------------------------------------------------------ 마감/주문서/삭제

@router.post("/{sid}/delete")
def delete_survey(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                  db: sqlite3.Connection = Depends(db_dep)):
    """응답도 함께 삭제(ON DELETE CASCADE). 생성자·관리자만."""
    survey = _get_survey(db, sid)
    if not _can_manage(user, survey):
        flash(request, "조사 생성자나 관리자만 삭제할 수 있습니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)
    with db:
        db.execute("DELETE FROM surveys WHERE id=?", (sid,))
    flash(request, f"조사를 삭제했습니다: {services.survey_title(survey)}")
    return RedirectResponse("/", status_code=303)


@router.post("/{sid}/close")
def close_survey(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                 db: sqlite3.Connection = Depends(db_dep)):
    survey = _get_survey(db, sid)
    if not _can_manage(user, survey):
        flash(request, "조사 생성자나 관리자만 마감할 수 있습니다")
        return RedirectResponse(f"/surveys/{sid}", status_code=303)
    services.close_survey(db, sid, due_only=False)
    return RedirectResponse(f"/surveys/{sid}/summary", status_code=303)


NOTIFY_COOLDOWN_MIN = 3  # 수동 알림 간격. 템플릿 문구와 같이 바꿀 것


@router.post("/{sid}/notify")
def notify_now(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
               db: sqlite3.Connection = Depends(db_dep)):
    """수동 리마인더: 미응답자 목록을 지금 텔레그램으로. 생성자·관리자만, 조사당 3분에 1회."""
    survey = _get_survey(db, sid)
    if not _can_manage(user, survey):
        flash(request, "조사 생성자나 관리자만 알림을 보낼 수 있습니다")
    elif survey["status"] != "open":
        flash(request, "마감된 조사입니다")
    elif not config.TELEGRAM_BOT_TOKEN:
        flash(request, "텔레그램이 설정되지 않았습니다 (.env TELEGRAM_BOT_TOKEN)")
    elif not services.build_summary(db, sid)["excluded"]:
        flash(request, "미응답자가 없어 보낼 내용이 없습니다")
    elif not services.notify_targets(db, survey["group_id"]):
        flash(request, "받을 텔레그램 채팅이 없습니다. 그룹 관리에서 chat_id를 넣으세요")
    else:
        limit = (datetime.now() - timedelta(minutes=NOTIFY_COOLDOWN_MIN)).strftime("%Y-%m-%d %H:%M:%S")
        with db:  # CAS: 3분 안에 이미 보냈으면 rowcount 0
            cur = db.execute("UPDATE surveys SET notified_at=? WHERE id=? AND (notified_at IS NULL OR notified_at <= ?)",
                             (now_str(), sid, limit))
        if not cur.rowcount:
            last = datetime.strptime(survey["notified_at"], "%Y-%m-%d %H:%M:%S")
            wait = NOTIFY_COOLDOWN_MIN * 60 - int((datetime.now() - last).total_seconds())
            flash(request, f"수동 알림은 {NOTIFY_COOLDOWN_MIN}분에 한 번만 보낼 수 있습니다 ({max(wait, 1)}초 후 가능)")
        else:
            n = services.announce(db, sid, "reminder")
            flash(request, f"리마인더를 보냈습니다 ({n}개 채팅)" if n else "전송에 실패했습니다. 서버 로그를 확인하세요")
    return RedirectResponse(f"/surveys/{sid}", status_code=303)


@router.get("/{sid}/summary")
def survey_summary(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                   db: sqlite3.Connection = Depends(db_dep)):
    services.close_survey(db, sid)
    survey = _get_survey(db, sid)
    if survey["status"] == "open":
        with db:
            services.refresh_prices(db, sid)
    summary = services.build_summary(db, sid)
    return render(request, "survey_summary.html", user=user, survey=survey, s=summary,
                  interim=(survey["status"] == "open"), can_manage=_can_manage(user, survey))
