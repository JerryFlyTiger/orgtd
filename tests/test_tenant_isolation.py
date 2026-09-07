"""跨租戶隔離：B 使用者不得以任何方式讀到或改到 A 的資料。

這是把單人系統改成多人時最容易漏的一類漏洞（IDOR）——原本 view 直接
以主鍵取物件，換個網址上的 id 就能碰別人的東西。
"""

import pytest
from sqlalchemy import select

from conftest import csrf_from, login
from db import SessionLocal
from models import Node, Tag, WeeklyReview


@pytest.fixture
def two_users(make_user):
    a_id, _ = make_user("alice@example.com", "Alice")
    b_id, _ = make_user("bob@example.com", "Bob")
    with SessionLocal() as session:
        secret = Node(user_id=a_id, kind="note", title="Alice 的機密筆記", body="薪資談判筆記")
        task = Node(user_id=a_id, kind="task", title="Alice 的任務", todo_state="NEXT")
        project = Node(user_id=a_id, kind="project", title="Alice 的專案")
        session.add_all([secret, task, project])
        session.commit()
        return {
            "a": a_id, "b": b_id,
            "note": secret.id, "task": task.id, "project": project.id,
        }


def test_b_cannot_read_a_note(client, two_users):
    login(client, "bob@example.com")
    assert client.get(f"/notes/{two_users['note']}").status_code == 404


def test_b_cannot_read_a_project(client, two_users):
    login(client, "bob@example.com")
    assert client.get(f"/projects/{two_users['project']}").status_code == 404


def test_b_cannot_change_a_task_state(client, two_users):
    login(client, "bob@example.com")
    token = csrf_from(client, "/")
    resp = client.post(
        f"/nodes/{two_users['task']}/state",
        data={"state": "DONE", "csrf_token": token},
    )
    assert resp.status_code == 404
    with SessionLocal() as session:
        assert session.get(Node, two_users["task"]).todo_state == "NEXT"


def test_b_cannot_archive_a_node(client, two_users):
    login(client, "bob@example.com")
    token = csrf_from(client, "/")
    resp = client.post(
        f"/nodes/{two_users['note']}/archive", data={"csrf_token": token}
    )
    assert resp.status_code == 404
    with SessionLocal() as session:
        assert session.get(Node, two_users["note"]).archived_at is None


def test_b_cannot_overwrite_a_note_body(client, two_users):
    login(client, "bob@example.com")
    token = csrf_from(client, "/")
    resp = client.post(
        f"/notes/{two_users['note']}/save",
        data={"title": "被竄改", "body": "壞掉了", "csrf_token": token},
    )
    assert resp.status_code == 404
    with SessionLocal() as session:
        assert session.get(Node, two_users["note"]).body == "薪資談判筆記"


def test_b_cannot_add_child_to_a_project(client, two_users):
    login(client, "bob@example.com")
    token = csrf_from(client, "/")
    resp = client.post(
        f"/projects/{two_users['project']}/add_child",
        data={"title": "插進來的任務", "csrf_token": token},
    )
    assert resp.status_code == 404


def test_b_search_does_not_return_a_notes(client, two_users):
    login(client, "bob@example.com")
    body = client.get("/notes/search?q=機密").get_data(as_text=True)
    assert "Alice 的機密筆記" not in body


def test_b_listing_pages_do_not_show_a_data(client, two_users):
    login(client, "bob@example.com")
    for path in ["/", "/agenda", "/projects/", "/notes/", "/dashboard"]:
        body = client.get(path).get_data(as_text=True)
        assert "Alice" not in body, path


def test_b_cannot_move_own_node_into_a_project(client, two_users):
    """把自己的節點搬進別人的專案樹，也必須擋下。"""
    with SessionLocal() as session:
        mine = Node(user_id=two_users["b"], kind="task", title="Bob 的任務")
        session.add(mine)
        session.commit()
        mine_id = mine.id

    login(client, "bob@example.com")
    token = csrf_from(client, "/")
    resp = client.post(
        f"/nodes/{mine_id}/move",
        data={"target_project_id": two_users["project"], "csrf_token": token},
    )
    assert resp.status_code == 404
    with SessionLocal() as session:
        assert session.get(Node, mine_id).parent_id is None


def test_same_tag_name_allowed_for_different_users(client, two_users):
    """標籤原本是全域唯一，多人之後同名標籤必須能各自存在。"""
    with SessionLocal() as session:
        session.add(Tag(user_id=two_users["a"], name="重要"))
        session.add(Tag(user_id=two_users["b"], name="重要"))
        session.commit()
        assert len(session.scalars(select(Tag).where(Tag.name == "重要")).all()) == 2


def test_same_review_week_allowed_for_different_users(client, two_users):
    """週回顧原本 week_start 全域唯一，第二個人做同一週會撞主鍵。"""
    import datetime

    week = datetime.date(2026, 3, 2)
    with SessionLocal() as session:
        session.add(WeeklyReview(user_id=two_users["a"], week_start=week, checklist={}))
        session.add(WeeklyReview(user_id=two_users["b"], week_start=week, checklist={}))
        session.commit()
        rows = session.scalars(
            select(WeeklyReview).where(WeeklyReview.week_start == week)
        ).all()
        assert len(rows) == 2
