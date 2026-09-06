"""Projects 專案樹：GTD 的 Organize 階段，org outline 大綱。"""

from flask import Blueprint, abort, redirect, render_template, request, url_for

from db import SessionLocal
from models import Node
from queries import fetch_subtree, has_next_action, is_descendant, list_projects

bp = Blueprint("projects", __name__, url_prefix="/projects")


@bp.route("/")
def index():
    with SessionLocal() as session:
        projects = list_projects(session)
        # GTD 卡點偵測：沒有 NEXT 行動的專案代表卡住了，需要在清單上標紅提醒。
        stalled = {p.id for p in projects if not has_next_action(session, p.id)}
        return render_template("projects.html", projects=projects, stalled=stalled)


@bp.route("/<int:project_id>")
def detail(project_id):
    with SessionLocal() as session:
        project = session.get(Node, project_id)
        if project is None or project.kind != "project" or project.archived_at is not None:
            abort(404)
        tree = fetch_subtree(session, project_id)
        other_projects = [p for p in list_projects(session) if p.id != project_id]
        return render_template(
            "project_detail.html", project=project, tree=tree[1:], other_projects=other_projects
        )


@bp.route("/<int:project_id>/add_child", methods=["POST"])
def add_child(project_id):
    title = request.form.get("title", "").strip()
    with SessionLocal() as session:
        project = session.get(Node, project_id)
        if project is None or project.kind != "project":
            abort(404)
        if title:
            max_position = max([c.position for c in project.children], default=-1)
            session.add(
                Node(
                    kind="task",
                    title=title,
                    todo_state="NEXT",
                    parent_id=project_id,
                    position=max_position + 1,
                )
            )
            session.commit()
    return redirect(url_for("projects.detail", project_id=project_id))
