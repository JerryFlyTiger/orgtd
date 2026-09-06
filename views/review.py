"""Weekly Review：GTD 的 Reflect 階段，8 步引導式週回顧，差異化殺手級功能。"""

import datetime

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import select

from db import SessionLocal
from models import Node, WeeklyReview
from queries import (
    fetch_upcoming,
    list_by_state,
    list_someday,
    projects_with_stalled_flag,
    weekly_pomodoro_stats,
)

bp = Blueprint("review", __name__, url_prefix="/review")

STEPS = [
    {"key": "clear_inbox", "title": "清空收集箱"},
    {"key": "review_next", "title": "檢視 Next Actions"},
    {"key": "review_waiting", "title": "檢視 Waiting For"},
    {"key": "review_projects", "title": "檢視 Projects"},
    {"key": "review_someday", "title": "檢視 Someday/Maybe"},
    {"key": "review_calendar", "title": "檢視未來一週行事曆"},
    {"key": "review_pomodoro", "title": "回顧本週番茄統計"},
    {"key": "reflect", "title": "自由反思與完成"},
]


def _week_start(today=None):
    today = today or datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())  # 週一為每週起點


def _get_or_create_review(session):
    week_start = _week_start()
    review = session.scalar(
        select(WeeklyReview).where(WeeklyReview.week_start == week_start)
    )
    if review is None:
        review = WeeklyReview(week_start=week_start, checklist={})
        session.add(review)
        session.commit()
    return review


def _step_context(session, n):
    if n == 1:
        items = session.scalars(
            select(Node)
            .where(Node.kind == "inbox", Node.archived_at.is_(None))
            .order_by(Node.created_at)
        ).all()
        return {"inbox_items": items}
    if n == 2:
        return {"next_actions": list_by_state(session, "NEXT")}
    if n == 3:
        return {"waiting_items": list_by_state(session, "WAITING")}
    if n == 4:
        return {"projects_stalled": projects_with_stalled_flag(session)}
    if n == 5:
        return {"someday_items": list_someday(session)}
    if n == 6:
        return {"upcoming": fetch_upcoming(session, days=7)}
    if n == 7:
        return {"pomodoro_stats": weekly_pomodoro_stats(session)}
    return {}


@bp.route("/")
def index():
    return redirect(url_for("review.step", n=1))


@bp.route("/step/<int:n>")
def step(n):
    if not (1 <= n <= len(STEPS)):
        abort(404)
    with SessionLocal() as session:
        review = _get_or_create_review(session)
        done_steps = sum(1 for s in STEPS if review.checklist.get(s["key"]))
        context = _step_context(session, n)
        return render_template(
            "review/wizard.html",
            n=n,
            steps=STEPS,
            review=review,
            done_steps=done_steps,
            **context,
        )


@bp.route("/step/<int:n>/complete", methods=["POST"])
def complete_step(n):
    if not (1 <= n <= len(STEPS)):
        abort(404)
    with SessionLocal() as session:
        review = _get_or_create_review(session)
        checklist = dict(review.checklist)
        checklist[STEPS[n - 1]["key"]] = True
        review.checklist = checklist
        if n == len(STEPS):
            review.reflection = request.form.get("reflection", "").strip()
            review.completed_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
    if n == len(STEPS):
        return redirect(url_for("review.done"))
    return redirect(url_for("review.step", n=n + 1))


@bp.route("/done")
def done():
    with SessionLocal() as session:
        review = _get_or_create_review(session)
        done_steps = sum(1 for s in STEPS if review.checklist.get(s["key"]))
        return render_template("review/done.html", review=review, done_steps=done_steps, steps=STEPS)
