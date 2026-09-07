"""端到端驗證「用 Emacs 編輯」這件事真的成立。

流程：網頁建立資料 → 產生 org 檔 → 在檔案上直接編輯（模擬 Emacs）
→ 匯入 → 資料庫反映出那次外部編輯，且節點身分沒有斷掉。
"""

import pathlib

from sqlalchemy import select

import orgfiles
import orgsync
from conftest import csrf_from, login
from db import SessionLocal
from models import Node, User


def _user(email):
    with SessionLocal() as session:
        return session.scalar(select(User).where(User.email == email))


def test_capture_creates_org_file_entry(client, make_user):
    make_user("e@example.com")
    login(client, "e@example.com")
    token = csrf_from(client, "/")
    client.post("/inbox/capture", data={"title": "買咖啡豆", "csrf_token": token})

    user = _user("e@example.com")
    text = (orgfiles.user_org_dir(user) / "inbox.org").read_text(encoding="utf-8")
    assert "買咖啡豆" in text
    assert ":ID:" in text


def test_external_edit_is_imported_back(client, make_user):
    make_user("f@example.com")
    login(client, "f@example.com")
    token = csrf_from(client, "/")
    client.post("/inbox/capture", data={"title": "原始標題", "csrf_token": token})

    user = _user("f@example.com")
    path = orgfiles.user_org_dir(user) / "inbox.org"

    # 模擬使用者在 Emacs 裡改標題並標成 TODO
    text = path.read_text(encoding="utf-8").replace("原始標題", "TODO 在 Emacs 改過的標題")
    path.write_text(text, encoding="utf-8")

    assert "inbox.org" in orgsync.externally_changed(user)

    with SessionLocal() as session:
        u = session.get(User, user.id)
        stats = orgsync.import_changed(session, u)
        assert "inbox.org" in stats["files"]
        node = session.scalar(select(Node).where(Node.user_id == u.id))
        assert node.title == "在 Emacs 改過的標題"
        assert node.todo_state == "TODO"


def test_node_identity_survives_external_edit(client, make_user):
    """外部改標題後仍是同一列 —— 番茄鐘紀錄與提醒才不會斷。"""
    make_user("g@example.com")
    login(client, "g@example.com")
    token = csrf_from(client, "/")
    client.post("/inbox/capture", data={"title": "身分測試", "csrf_token": token})

    user = _user("g@example.com")
    with SessionLocal() as session:
        original_id = session.scalar(select(Node).where(Node.user_id == user.id)).id

    path = orgfiles.user_org_dir(user) / "inbox.org"
    path.write_text(
        path.read_text(encoding="utf-8").replace("身分測試", "完全不同的名字"),
        encoding="utf-8",
    )

    with SessionLocal() as session:
        u = session.get(User, user.id)
        orgsync.import_changed(session, u)
        nodes = session.scalars(select(Node).where(Node.user_id == u.id)).all()
        assert len(nodes) == 1
        assert nodes[0].id == original_id  # 同一列，不是新增一筆


def test_node_added_manually_in_emacs_is_picked_up(client, make_user):
    """使用者在 Emacs 手打一個沒有 :ID: 的節點，匯入時要自動納管。"""
    make_user("h@example.com")
    user = _user("h@example.com")
    path = orgfiles.user_org_dir(user) / "inbox.org"
    path.write_text(
        path.read_text(encoding="utf-8") + "* NEXT 手動加在 Emacs 裡的事情\n",
        encoding="utf-8",
    )

    with SessionLocal() as session:
        u = session.get(User, user.id)
        stats = orgsync.import_changed(session, u)
        assert stats["created"] == 1
        node = session.scalar(select(Node).where(Node.user_id == u.id))
        assert node.title == "手動加在 Emacs 裡的事情"
        assert node.todo_state == "NEXT"
        assert node.org_id  # 已自動配發識別碼


def test_each_user_gets_separate_directory(make_user):
    make_user("i@example.com")
    make_user("j@example.com")
    a, b = _user("i@example.com"), _user("j@example.com")
    assert orgfiles.user_org_dir(a) != orgfiles.user_org_dir(b)
    assert orgfiles.user_org_dir(a).exists()
    assert orgfiles.user_org_dir(b).exists()


def test_custom_dir_rejects_dangerous_paths():
    """空白、相對路徑、根目錄、家目錄本身、系統目錄一律拒絕。

    /etc 這一項是回歸測試：macOS 上它是 /private/etc 的符號連結，
    早先用字面前綴比對的版本會漏掉。
    """
    bad_paths = ["", "relative/path", "/", str(pathlib.Path.home()), "/etc", "/usr/local"]
    for bad in bad_paths:
        path, err = orgfiles.validate_custom_dir(bad)
        assert err is not None, bad
        assert path is None, bad


def test_custom_dir_accepts_path_under_home():
    path, err = orgfiles.validate_custom_dir("~/org")
    assert err is None, err
    assert path == pathlib.Path.home().resolve() / "org"


def test_custom_dir_expands_tilde_and_nested():
    path, err = orgfiles.validate_custom_dir("~/Documents/gtd/org")
    assert err is None, err
    assert path.is_relative_to(pathlib.Path.home().resolve())
