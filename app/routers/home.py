"""홈: lazy 스케줄 생성/마감이 걸리는 공통 진입점."""
import sqlite3

from fastapi import APIRouter, Depends, Request

from .. import services
from ..db import db_dep
from ..deps import current_user, render

router = APIRouter(tags=["home"])


@router.get("/")
def home(request: Request, user: sqlite3.Row = Depends(current_user),
         db: sqlite3.Connection = Depends(db_dep)):
    services.generate_due_surveys(db)

    group_ids = services.user_visible_group_ids(db, user["id"])
    surveys_open, surveys_closed = [], []
    if group_ids:
        ph = ",".join("?" * len(group_ids))
        ids = [r["id"] for r in db.execute(
            f"SELECT id FROM surveys WHERE group_id IN ({ph}) AND status='open'", group_ids)]
        services.lazy_close_due(db, ids)

        base = (
            "SELECT s.*, g.name AS group_name, c.name AS cafe_name, "
            "  (SELECT COUNT(*) FROM survey_responses r "
            "   WHERE r.survey_id=s.id AND r.participant_user_id=?) AS my_responded "
            "FROM surveys s JOIN groups g ON g.id=s.group_id JOIN cafes c ON c.id=s.cafe_id "
            f"WHERE s.group_id IN ({ph}) "
        )
        # 홈 정렬 결정사항: 그룹 이름 오름차순
        surveys_open = db.execute(
            base + "AND s.status='open' ORDER BY g.name ASC, s.survey_date ASC",
            [user["id"], *group_ids]).fetchall()
        surveys_closed = db.execute(
            base + "AND s.status='closed' ORDER BY s.survey_date DESC, s.id DESC LIMIT 10",
            [user["id"], *group_ids]).fetchall()

    return render(request, "home.html", user=user,
                  surveys_open=surveys_open, surveys_closed=surveys_closed)
