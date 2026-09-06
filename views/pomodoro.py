"""Pomodoro 番茄鐘：GTD 的 Engage 階段，org-clock 的具體化。"""

import datetime

from flask import Blueprint, abort, jsonify, render_template, request
from sqlalchemy import select

from constants import POMODORO_KINDS
from db import SessionLocal
from models import Node, PomodoroSession
from queries import count_today_pomodoros, get_settings

bp = Blueprint("pomodoro", __name__, url_prefix="/pomodoro")


@bp.route("/")
def index():
    node_id = request.args.get("node", type=int)
    with SessionLocal() as session:
        today_count = count_today_pomodoros(session)

        if node_id is None:
            next_actions = session.scalars(
                select(Node)
                .where(
                    Node.kind == "task",
                    Node.todo_state == "NEXT",
                    Node.archived_at.is_(None),
                )
                .order_by(Node.created_at)
            ).all()
            return render_template(
                "pomodoro_picker.html", next_actions=next_actions, today_count=today_count
            )

        node = session.get(Node, node_id)
        if node is None or node.kind != "task" or node.archived_at is not None:
            abort(404)
        settings = get_settings(session)
        return render_template(
            "pomodoro.html", node=node, settings=settings, today_count=today_count
        )


def _record(completed):
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    node_id = data.get("node_id")
    planned_minutes = data.get("planned_minutes")
    actual_seconds = data.get("actual_seconds")

    if kind not in POMODORO_KINDS:
        abort(400)
    if not isinstance(planned_minutes, int) or not isinstance(actual_seconds, int):
        abort(400)
    if planned_minutes <= 0 or actual_seconds < 0:
        abort(400)

    with SessionLocal() as session:
        node = None
        if kind == "focus" and node_id is not None:
            node = session.get(Node, node_id)
            if node is None:
                abort(404)

        now = datetime.datetime.now(datetime.timezone.utc)
        started_at = now - datetime.timedelta(seconds=actual_seconds)
        session.add(
            PomodoroSession(
                node_id=node.id if node else None,
                started_at=started_at,
                ended_at=now,
                planned_minutes=planned_minutes,
                actual_seconds=actual_seconds,
                completed=completed,
                kind=kind,
            )
        )
        session.commit()
    return jsonify({"ok": True})


@bp.route("/complete", methods=["POST"])
def complete():
    return _record(completed=True)


@bp.route("/abandon", methods=["POST"])
def abandon():
    return _record(completed=False)
