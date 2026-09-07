"""Projects 專案樹：GTD 的 Organize 階段，org outline 大綱。"""

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required

from db import SessionLocal
from models import Node
from queries import fetch_subtree, has_next_action, list_projects
from views._scope import owned_node, uid

bp = Blueprint("projects", __name__, url_prefix="/projects")


@bp.route("/")
@login_required
def index():
    with SessionLocal() as session:
        u = uid()
        projects = list_projects(session, u)
        # GTD 卡點偵測：沒有 NEXT 行動的專案代表卡住了，需要在清單上標紅提醒。
        stalled = {p.id for p in projects if not has_next_action(session, u, p.id)}
        return render_template("projects.html", projects=projects, stalled=stalled)


@bp.route("/<int:project_id>")
@login_required
def detail(project_id):
    with SessionLocal() as session:
        u = uid()
        project = owned_node(session, project_id, kind="project", allow_archived=False)
        tree = fetch_subtree(session, u, project_id)
        other_projects = [p for p in list_projects(session, u) if p.id != project_id]
        return render_template(
            "project_detail.html", project=project, tree=tree[1:], other_projects=other_projects
        )


@bp.route("/<int:project_id>/add_child", methods=["POST"])
@login_required
def add_child(project_id):
    title = request.form.get("title", "").strip()
    with SessionLocal() as session:
        project = owned_node(session, project_id, kind="project")
        if title:
            max_position = max([c.position for c in project.children], default=-1)
            node = Node(
                user_id=uid(),
                kind="task",
                title=title,
                todo_state="NEXT",
                parent_id=project_id,
                position=max_position + 1,
            )
            session.add(node)
            session.commit()
    return redirect(url_for("projects.detail", project_id=project_id))
