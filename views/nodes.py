"""跨頁共用的節點操作：切換 TODO 狀態機、封存。"""

import calendar
import datetime

from flask import Blueprint, abort, redirect, request, url_for
from flask_login import login_required
from sqlalchemy import func, select

from constants import REPEAT_RULES, TODO_STATES
from db import SessionLocal
from models import Node, Tag
from queries import is_descendant
from views._scope import owned_node, uid

bp = Blueprint("nodes", __name__, url_prefix="/nodes")


def _advance(dt, rule):
    """依 org 式重複規則把日期往後推一輪。"""
    if rule == "+1d":
        return dt + datetime.timedelta(days=1)
    if rule == "+1w":
        return dt + datetime.timedelta(weeks=1)
    if rule == "+1m":
        # 月份加一，日超出目標月天數時退到當月最後一天（避免 2/30 這種不存在的日期）。
        month = dt.month + 1
        year = dt.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        last_day = calendar.monthrange(year, month)[1]
        return dt.replace(year=year, month=month, day=min(dt.day, last_day))
    return dt


@bp.route("/<int:node_id>/state", methods=["POST"])
@login_required
def set_state(node_id):
    state = request.form.get("state", "")
    if state not in TODO_STATES:
        abort(404)
    with SessionLocal() as session:
        node = owned_node(session, node_id, allow_archived=False)
        if state == "DONE" and node.repeat_rule in REPEAT_RULES:
            # org 的重複任務：完成不是關閉，而是把排程/截止日往後推一輪，狀態留在 NEXT。
            if node.scheduled_at:
                node.scheduled_at = _advance(node.scheduled_at, node.repeat_rule)
            if node.deadline_at:
                node.deadline_at = _advance(node.deadline_at, node.repeat_rule)
            node.todo_state = "NEXT"
            node.notified_at = None
        else:
            node.todo_state = state
            node.done_at = (
                datetime.datetime.now(datetime.timezone.utc) if state == "DONE" else None
            )
        session.commit()
    return redirect(request.referrer or url_for("agenda.index"))


@bp.route("/<int:node_id>/remind", methods=["POST"])
@login_required
def set_reminder(node_id):
    raw = request.form.get("remind_at", "")
    repeat_rule = request.form.get("repeat_rule", "") or None
    if repeat_rule is not None and repeat_rule not in REPEAT_RULES:
        abort(404)
    with SessionLocal() as session:
        node = owned_node(session, node_id, allow_archived=False)
        node.remind_at = datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M") if raw else None
        node.notified_at = None
        node.repeat_rule = repeat_rule
        session.commit()
    return redirect(request.referrer or url_for("agenda.index"))


@bp.route("/<int:node_id>/move", methods=["POST"])
@login_required
def move(node_id):
    target_id = request.form.get("target_project_id", type=int)
    with SessionLocal() as session:
        node = owned_node(session, node_id, allow_archived=False)
        # 目標專案也必須是自己的，否則等於把節點搬進別人的樹。
        target = owned_node(session, target_id, kind="project", allow_archived=False)
        if is_descendant(session, uid(), target.id, node.id):
            # 目標是自己的子孫，搬過去會形成循環，拒絕。
            abort(404)
        max_position = session.scalar(
            select(func.coalesce(func.max(Node.position), -1)).where(
                Node.user_id == uid(), Node.parent_id == target.id
            )
        )
        node.parent_id = target.id
        node.position = max_position + 1
        session.commit()
    return redirect(request.referrer or url_for("projects.index"))


@bp.route("/<int:node_id>/archive", methods=["POST"])
@login_required
def archive(node_id):
    with SessionLocal() as session:
        node = owned_node(session, node_id)
        node.archived_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
    return redirect(request.referrer or url_for("agenda.index"))


@bp.route("/<int:node_id>/tags", methods=["POST"])
@login_required
def add_tag(node_id):
    name = request.form.get("name", "").strip()
    with SessionLocal() as session:
        node = owned_node(session, node_id)
        if name:
            # 標籤現在是每人一組，查詢與新建都要帶 user_id。
            tag = session.scalar(
                select(Tag).where(Tag.user_id == uid(), Tag.name == name)
            )
            if tag is None:
                tag = Tag(user_id=uid(), name=name)
                session.add(tag)
            if tag not in node.tags:
                node.tags.append(tag)
            session.commit()
    return redirect(request.referrer or url_for("agenda.index"))


@bp.route("/<int:node_id>/tags/<int:tag_id>/remove", methods=["POST"])
@login_required
def remove_tag(node_id, tag_id):
    with SessionLocal() as session:
        node = owned_node(session, node_id)
        tag = session.scalar(
            select(Tag).where(Tag.id == tag_id, Tag.user_id == uid())
        )
        if tag is None:
            abort(404)
        if tag in node.tags:
            node.tags.remove(tag)
            session.commit()
    return redirect(request.referrer or url_for("agenda.index"))
