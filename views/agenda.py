"""Agenda 今日視圖：GTD 的 Engage 階段。"""

from flask import Blueprint, jsonify, render_template

from db import SessionLocal
from queries import claim_due_reminders, fetch_agenda

bp = Blueprint("agenda", __name__)


@bp.route("/agenda")
def index():
    with SessionLocal() as session:
        data = fetch_agenda(session)
        return render_template("agenda.html", **data)


@bp.route("/api/due")
def api_due():
    """頁面開著時由 reminders.js 每分鐘輪詢：回傳到點提醒，並就地標記已通知。"""
    with SessionLocal() as session:
        due = claim_due_reminders(session)
        return jsonify([{"id": n.id, "title": n.title} for n in due])
