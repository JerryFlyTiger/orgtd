"""Notes 筆記：GTD 的參考資料，org 的一般 headline + 內文。"""

import datetime

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import select

from db import SessionLocal
from models import Node
from queries import search_notes

bp = Blueprint("notes", __name__, url_prefix="/notes")


@bp.route("/")
def index():
    with SessionLocal() as session:
        notes = session.scalars(
            select(Node)
            .where(Node.kind == "note", Node.archived_at.is_(None))
            .order_by(Node.updated_at.desc())
        ).all()
        return render_template("notes.html", notes=notes, results=None, q="")


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    with SessionLocal() as session:
        results = search_notes(session, q)
        return render_template("notes.html", notes=None, results=results, q=q)


@bp.route("/<int:node_id>")
def detail(node_id):
    with SessionLocal() as session:
        node = session.get(Node, node_id)
        if node is None or node.kind != "note" or node.archived_at is not None:
            abort(404)
        return render_template("note_detail.html", node=node)


@bp.route("/<int:node_id>/save", methods=["POST"])
def save(node_id):
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "")
    with SessionLocal() as session:
        node = session.get(Node, node_id)
        if node is None or node.kind != "note" or node.archived_at is not None:
            abort(404)
        if title:
            node.title = title
        node.body = body
        session.commit()
    return redirect(url_for("notes.detail", node_id=node_id))


@bp.route("/<int:node_id>/archive", methods=["POST"])
def archive(node_id):
    with SessionLocal() as session:
        node = session.get(Node, node_id)
        if node is None or node.kind != "note":
            abort(404)
        node.archived_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
    return redirect(url_for("notes.index"))
