"""測試共用設定。

db.py 在 import 時就建好 engine，所以資料庫位址必須在任何專案模組被
匯入之前設定完成——因此這裡先動環境變數，才 import app。
"""

import os
import pathlib
import sys
import tempfile

os.environ.setdefault(
    "ORGTD_DATABASE_URL", "postgresql+psycopg://jerrychen@localhost:5432/orgtd_test"
)
os.environ["ORGTD_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ORGTD_DEBUG"] = "0"
os.environ["ORGTD_COOKIE_SECURE"] = "0"  # 測試用 http，不然 cookie 送不出去
os.environ["ORGTD_ALLOW_REGISTRATION"] = "1"
os.environ["ORGTD_ORG_ROOT"] = tempfile.mkdtemp(prefix="orgtd-test-")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

import config  # noqa: E402
from db import SessionLocal, engine  # noqa: E402
from models import User  # noqa: E402
from security import hash_password  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app():
    from app import create_app

    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return application


@pytest.fixture(autouse=True)
def clean_db():
    """每個測試前清空所有租戶資料（保留 schema）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, nodes, tags, node_tags, pomodoro_sessions, "
                "weekly_reviews, settings, oauth_accounts RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def make_user():
    def _make(email, name=None):
        with SessionLocal() as session:
            user = User(
                email=email,
                display_name=name or email.split("@")[0],
                password_hash=hash_password(PASSWORD),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            # 給每個測試使用者一個獨立的暫存 org 資料夾
            import orgfiles

            orgfiles.provision_user_directory(user)
            return user.id, user.uuid

    return _make


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, email, password=PASSWORD):
    """走真正的登入表單，順便把 CSRF token 帶上。"""
    page = client.get("/login")
    token = _extract_csrf(page.get_data(as_text=True))
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def _extract_csrf(html):
    import re

    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else ""


def csrf_from(client, path):
    page = client.get(path)
    return _extract_csrf(page.get_data(as_text=True))
