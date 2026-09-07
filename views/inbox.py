"""Inbox 收集箱：GTD 的 Capture 與 Clarify 階段。"""

import datetime

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import select

from db import SessionLocal
from models import Node
from views._scope import owned_node, uid

bp = Blueprint("inbox", __name__)

_CLARIFY_KINDS = ("task", "project", "note", "someday")


@bp.route("/")
@login_required
def index():
    with SessionLocal() as session:
        items = session.scalars(
            select(Node)
            .where(
                Node.user_id == uid(),
                Node.kind == "inbox",
                Node.archived_at.is_(None),
            )
            .order_by(Node.created_at)
        ).all()
        return render_template("inbox.html", items=items)


@bp.route("/inbox/capture", methods=["POST"])
@login_required
def capture():
    title = request.form.get("title", "").strip()
    with SessionLocal() as session:
        if title:
            node = Node(user_id=uid(), kind="inbox", title=title)
            session.add(node)
            session.commit()
    return redirect(url_for("inbox.index"))


def _parse_date(value):
    """把 <input type=date> 的 yyyy-mm-dd 轉成當日 00:00 的 datetime，空字串回傳 None。"""
    if not value:
        return None
    return datetime.datetime.strptime(value, "%Y-%m-%d")


@bp.route("/inbox/<int:node_id>/clarify", methods=["POST"])
@login_required
def clarify(node_id):
    kind = request.form.get("kind", "")
    if kind not in _CLARIFY_KINDS:
        abort(404)
    with SessionLocal() as session:
        node = owned_node(session, node_id, kind="inbox")
        node.kind = kind
        if kind == "task":
            # 新任務預設視為可立即執行的下一步行動，符合 GTD 的 NEXT 精神。
            node.todo_state = "NEXT"
            node.scheduled_at = _parse_date(request.form.get("scheduled_at"))
            node.deadline_at = _parse_date(request.form.get("deadline_at"))
        session.commit()
    return redirect(url_for("inbox.index"))


@bp.route("/inbox/<int:node_id>/discard", methods=["POST"])
@login_required
def discard(node_id):
    with SessionLocal() as session:
        node = owned_node(session, node_id, kind="inbox")
        node.archived_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
    return redirect(url_for("inbox.index"))
