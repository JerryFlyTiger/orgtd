"""CSRF 防護：原本整個專案沒有任何 token，有登入狀態後那是可被外站觸發的寫入漏洞。"""

from conftest import login
from db import SessionLocal
from models import Node


def test_post_without_token_is_rejected(client, make_user):
    make_user("c@example.com")
    login(client, "c@example.com")
    resp = client.post("/inbox/capture", data={"title": "沒有 token 的請求"})
    assert resp.status_code == 400
    with SessionLocal() as session:
        assert session.query(Node).count() == 0


def test_post_with_token_succeeds(client, make_user):
    from conftest import csrf_from

    make_user("d@example.com")
    login(client, "d@example.com")
    token = csrf_from(client, "/")
    resp = client.post(
        "/inbox/capture", data={"title": "正常的請求", "csrf_token": token}
    )
    assert resp.status_code == 302
    with SessionLocal() as session:
        assert session.query(Node).count() == 1
