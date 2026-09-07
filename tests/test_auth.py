"""註冊、登入、登出與存取控制。"""

from conftest import PASSWORD, csrf_from, login


def test_anonymous_is_redirected_to_login(client):
    for path in ["/", "/agenda", "/projects/", "/dashboard", "/settings/", "/notes/"]:
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "/login" in resp.headers["Location"], path


def test_register_then_access(client):
    token = csrf_from(client, "/register")
    resp = client.post(
        "/register",
        data={
            "email": "new@example.com",
            "display_name": "新使用者",
            "password": "a-long-enough-passphrase",
            "password_confirm": "a-long-enough-passphrase",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 302
    assert client.get("/").status_code == 200


def test_register_rejects_short_password(client):
    token = csrf_from(client, "/register")
    resp = client.post(
        "/register",
        data={
            "email": "short@example.com",
            "display_name": "x",
            "password": "short",
            "password_confirm": "short",
            "csrf_token": token,
        },
    )
    assert "至少 12 個字元" in resp.get_data(as_text=True)


def test_register_rejects_duplicate_email(client, make_user):
    make_user("dup@example.com")
    token = csrf_from(client, "/register")
    resp = client.post(
        "/register",
        data={
            "email": "dup@example.com",
            "display_name": "x",
            "password": "a-long-enough-passphrase",
            "password_confirm": "a-long-enough-passphrase",
            "csrf_token": token,
        },
    )
    assert "已經註冊過" in resp.get_data(as_text=True)


def test_login_success_and_wrong_password(client, make_user):
    make_user("a@example.com")
    assert login(client, "a@example.com").status_code == 302
    assert client.get("/").status_code == 200

    client.get("/logout")  # GET 不是登出路由，狀態應該不變
    assert client.get("/").status_code == 200

    token = csrf_from(client, "/")
    client.post("/logout", data={"csrf_token": token})
    assert client.get("/").status_code == 302


def test_wrong_password_message_does_not_leak_account_existence(client, make_user):
    make_user("real@example.com")
    r1 = login(client, "real@example.com", "wrong-password-here")
    r2 = login(client, "nobody@example.com", "wrong-password-here")
    assert "email 或密碼不正確" in r1.get_data(as_text=True)
    assert "email 或密碼不正確" in r2.get_data(as_text=True)


def test_oauth_only_account_cannot_password_login(client, make_user):
    """password_hash 為 NULL 的帳號不得因為「空密碼比對成功」而登入。"""
    from db import SessionLocal
    from models import User
    from sqlalchemy import select

    make_user("oauth@example.com")
    with SessionLocal() as session:
        u = session.scalar(select(User).where(User.email == "oauth@example.com"))
        u.password_hash = None
        session.commit()

    for attempt in ["", PASSWORD, "anything"]:
        login(client, "oauth@example.com", attempt)
        assert client.get("/").status_code == 302
