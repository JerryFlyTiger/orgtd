"""Notes 筆記：GTD 的參考資料，org 的一般 headline + 內文。"""

import datetime

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import select

from db import SessionLocal
from models import Node
from queries import search_notes
from views._scope import owned_node, uid

bp = Blueprint("notes", __name__, url_prefix="/notes")


@bp.route("/")
@login_required
def index():
    with SessionLocal() as session:
        notes = session.scalars(
            select(Node)
            .where(
                Node.user_id == uid(),
                Node.kind == "note",
                Node.archived_at.is_(None),
            )
            .order_by(Node.updated_at.desc())
        ).all()
        return render_template("notes.html", notes=notes, results=None, q="")


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    with SessionLocal() as session:
        results = search_notes(session, uid(), q)
        return render_template("notes.html", notes=None, results=results, q=q)


@bp.route("/<int:node_id>")
@login_required
def detail(node_id):
    with SessionLocal() as session:
        node = owned_node(session, node_id, kind="note", allow_archived=False)
        return render_template("note_detail.html", node=node)


@bp.route("/<int:node_id>/save", methods=["POST"])
@login_required
def save(node_id):
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "")
    with SessionLocal() as session:
        node = owned_node(session, node_id, kind="note", allow_archived=False)
        if title:
            node.title = title
        node.body = body
        session.commit()
    return redirect(url_for("notes.detail", node_id=node_id))


@bp.route("/<int:node_id>/archive", methods=["POST"])
@login_required
def archive(node_id):
    with SessionLocal() as session:
        node = owned_node(session, node_id, kind="note")
        node.archived_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
    return redirect(url_for("notes.index"))
