"""반복 스케줄: 등록은 모든 사용자, 수정·중지는 생성자와 admin."""
import sqlite3

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import config, services
from ..db import db_dep
from ..deps import current_user, flash, render

router = APIRouter(prefix="/schedules", tags=["schedules"])

@router.get("")
def schedules_list(request: Request, user: sqlite3.Row = Depends(current_user),
                   db: sqlite3.Connection = Depends(db_dep)):
    scheds = db.execute(
        "SELECT s.*, g.name AS group_name, c.name AS cafe_name, u.name AS creator_name "
        "FROM survey_schedules s JOIN groups g ON g.id=s.group_id "
        "JOIN cafes c ON c.id=s.cafe_id JOIN users u ON u.id=s.created_by "
        "ORDER BY s.enabled DESC, s.weekday, s.deadline_time").fetchall()
    groups = db.execute("SELECT * FROM groups ORDER BY name").fetchall()
    cafes = db.execute("SELECT * FROM cafes WHERE is_active=1 ORDER BY name").fetchall()
    return render(request, "schedules.html", user=user, scheds=scheds, groups=groups, cafes=cafes,
                  weekdays=services.KR_WEEKDAYS,
                  remind_minutes="·".join(str(m) for m in sorted(config.REMIND_MINUTES, reverse=True)) or "(없음)")


@router.post("")
def schedule_create(request: Request, group_id: int = Form(...), cafe_id: int = Form(...),
                    weekday: int = Form(...), deadline_time: str = Form(...), open_time: str = Form("00:00"),
                    title_pattern: str = Form(""), allow_guests: str = Form(""), remind_minutes: str = Form(""),
                    user: sqlite3.Row = Depends(current_user),
                    db: sqlite3.Connection = Depends(db_dep)):
    if not (0 <= weekday <= 6):
        raise HTTPException(400)
    try:
        datetime.strptime(deadline_time, "%H:%M")
        datetime.strptime(open_time, "%H:%M")
    except ValueError:
        flash(request, "시각 형식이 잘못됐습니다 (HH:MM)")
        return RedirectResponse("/schedules", status_code=303)
    if open_time >= deadline_time:
        flash(request, "생성 시각은 마감 시각보다 앞이어야 합니다")
        return RedirectResponse("/schedules", status_code=303)
    try:
        remind = services.remind_minutes_from_form(remind_minutes)
    except ValueError as e:
        flash(request, str(e))
        return RedirectResponse("/schedules", status_code=303)
    if user["role"] != "admin" and not services.is_effective_member(db, group_id, user["id"]):
        flash(request, "자기가 속한 그룹(또는 그 상위 그룹)에만 스케줄을 걸 수 있습니다")
        return RedirectResponse("/schedules", status_code=303)
    try:
        with db:
            db.execute(
                "INSERT INTO survey_schedules "
                "(group_id, cafe_id, weekday, open_time, deadline_time, allow_guests, title_pattern, remind_minutes, created_by) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (group_id, cafe_id, weekday, open_time, deadline_time,
                 1 if allow_guests else 0, title_pattern.strip() or None, remind, user["id"]))
    except sqlite3.IntegrityError:  # 없는 카페 id 등 (FK)
        flash(request, "그룹 또는 카페를 찾을 수 없습니다")
        return RedirectResponse("/schedules", status_code=303)
    flash(request, f"스케줄이 등록됐습니다. 매주 {services.KR_WEEKDAYS[weekday]}요일 {open_time}에 조사가 생성되고 {deadline_time}에 마감됩니다")
    return RedirectResponse("/schedules", status_code=303)


@router.post("/{sid}/delete")
def schedule_delete(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                    db: sqlite3.Connection = Depends(db_dep)):
    """이미 만들어진 조사는 남는다(schedule_id만 NULL)."""
    s = db.execute("SELECT * FROM survey_schedules WHERE id=?", (sid,)).fetchone()
    if s is None:
        raise HTTPException(404)
    if user["id"] != s["created_by"] and user["role"] != "admin":
        flash(request, "스케줄 생성자나 관리자만 삭제할 수 있습니다")
    else:
        with db:
            db.execute("DELETE FROM survey_schedules WHERE id=?", (sid,))
        flash(request, "스케줄을 삭제했습니다. 이미 만들어진 조사는 그대로 남습니다")
    return RedirectResponse("/schedules", status_code=303)


@router.post("/{sid}/toggle")
def schedule_toggle(sid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                    db: sqlite3.Connection = Depends(db_dep)):
    s = db.execute("SELECT * FROM survey_schedules WHERE id=?", (sid,)).fetchone()
    if s is None:
        raise HTTPException(404)
    if user["id"] != s["created_by"] and user["role"] != "admin":
        flash(request, "스케줄 생성자나 관리자만 변경할 수 있습니다")
    else:
        with db:
            db.execute("UPDATE survey_schedules SET enabled=? WHERE id=?",
                       (0 if s["enabled"] else 1, sid))
        flash(request, "스케줄을 " + ("중지했습니다" if s["enabled"] else "다시 켰습니다"))
    return RedirectResponse("/schedules", status_code=303)
