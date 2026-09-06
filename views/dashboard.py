"""Dashboard 統計：系統健康度總覽，串起前面所有里程碑的資料。"""

from flask import Blueprint, render_template

from db import SessionLocal
from queries import (
    count_inbox,
    count_today_pomodoros,
    current_streak,
    list_by_state,
    list_someday,
    projects_with_stalled_flag,
    weekly_pomodoro_stats,
)

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
def index():
    with SessionLocal() as session:
        week_stats = weekly_pomodoro_stats(session)
        projects = projects_with_stalled_flag(session)

        return render_template(
            "dashboard.html",
            streak=current_streak(session),
            today_pomodoros=count_today_pomodoros(session),
            week_stats=week_stats,
            week_total_pomodoros=sum(row["pomodoros"] for row in week_stats),
            week_total_minutes=sum((row["total_seconds"] or 0) for row in week_stats) // 60,
            inbox_count=count_inbox(session),
            next_count=len(list_by_state(session, "NEXT")),
            waiting_count=len(list_by_state(session, "WAITING")),
            someday_count=len(list_someday(session)),
            project_count=len(projects),
            stalled_count=sum(1 for _, stalled in projects if stalled),
        )
