"""Calendar 行事曆：SCHEDULED / DEADLINE 落格檢視。"""

import calendar
import datetime
from collections import defaultdict

from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import login_required

from db import SessionLocal
from queries import fetch_month_nodes
from views._scope import uid

bp = Blueprint("calendar", __name__, url_prefix="/calendar")

_MIN_YEAR, _MAX_YEAR = 1970, 2999


@bp.route("/")
@login_required
def today():
    now = datetime.date.today()
    return redirect(url_for("calendar.month_view", year=now.year, month=now.month))


@bp.route("/<int:year>/<int:month>")
@login_required
def month_view(year, month):
    if not (_MIN_YEAR <= year <= _MAX_YEAR) or not (1 <= month <= 12):
        abort(404)

    with SessionLocal() as session:
        nodes = fetch_month_nodes(session, uid(), year, month)

    by_day = defaultdict(list)
    for node in nodes:
        if node.scheduled_at and node.scheduled_at.year == year and node.scheduled_at.month == month:
            by_day[node.scheduled_at.day].append(("排程", node))
        if node.deadline_at and node.deadline_at.year == year and node.deadline_at.month == month:
            by_day[node.deadline_at.day].append(("截止", node))

    weeks = calendar.monthcalendar(year, month)  # 週一為每列第一天

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        weeks=weeks,
        by_day=by_day,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        today=datetime.date.today(),
    )
