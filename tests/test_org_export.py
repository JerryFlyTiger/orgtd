"""org 檔匯出的格式正確性與存取控制。

匯出是單向的：資料庫算出 org 純文字，伺服器不留檔。這裡驗證產出的
格式真的是 Emacs / VSCode 讀得懂的 org-mode，而不只是「有輸出東西」。
"""

import datetime
import io
import zipfile

import pytest

import orgfiles
from conftest import csrf_from, login
from db import SessionLocal
from models import Node, Tag


class FakeTag:
    def __init__(self, name):
        self.name = name


class FakeNode:
    """不進資料庫的輕量節點，用來單獨測渲染邏輯。"""

    def __init__(self, **kw):
        defaults = dict(
            title="", body=None, todo_state=None, priority=None, tags=[],
            scheduled_at=None, deadline_at=None, remind_at=None,
            repeat_rule=None, org_id="test-id", kind="task",
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


def render(node, children=()):
    kids = {id(node): list(children)}
    for c in children:
        kids.setdefault(id(c), [])
    return orgfiles.render_node(node, 1, lambda n: kids[id(n)])


# ---------------------------------------------------------------------------
# headline 格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["TODO", "NEXT", "WAITING", "DONE", "CANCELLED"])
def test_todo_state_written_before_title(state):
    out = render(FakeNode(title="事情", todo_state=state))
    assert out.startswith(f"* {state} 事情")


def test_priority_cookie_format():
    out = render(FakeNode(title="事情", todo_state="TODO", priority="A"))
    assert out.startswith("* TODO [#A] 事情")


def test_no_state_means_bare_headline():
    out = render(FakeNode(title="只是一個標題"))
    assert out.startswith("* 只是一個標題")


def test_chinese_title_survives_intact():
    out = render(FakeNode(title="買菜與晚餐準備"))
    assert "* 買菜與晚餐準備" in out


def test_newline_in_title_is_flattened():
    """標題裡的換行會把一個 headline 拆成兩行，破壞整份檔案的結構。"""
    out = render(FakeNode(title="第一行\n第二行"))
    headline = out.split("\n")[0]
    assert headline == "* 第一行 第二行"


def test_tags_use_org_colon_syntax():
    out = render(FakeNode(title="事情", tags=[FakeTag("家事"), FakeTag("errand")]))
    assert ":家事:errand:" in out.split("\n")[0]


# ---------------------------------------------------------------------------
# 時間戳與 PROPERTIES
# ---------------------------------------------------------------------------


def test_scheduled_deadline_and_repeat_rule():
    out = render(FakeNode(
        title="週會",
        scheduled_at=datetime.datetime(2026, 3, 2, 14, 30).astimezone(),
        deadline_at=datetime.datetime(2026, 3, 9).astimezone(),
        repeat_rule="+1w",
    ))
    assert "SCHEDULED: <2026-03-02 Mon 14:30 +1w>" in out
    # 午夜的截止日不該印出 00:00，org 慣例是純日期
    assert "DEADLINE: <2026-03-09 Mon>" in out


def test_midnight_scheduled_omits_time():
    out = render(FakeNode(
        title="全天事項",
        scheduled_at=datetime.datetime(2026, 3, 2, 0, 0).astimezone(),
    ))
    assert "<2026-03-02 Mon>" in out
    assert "00:00" not in out


def test_properties_drawer_carries_org_id():
    out = render(FakeNode(title="事情", org_id="abc-123", kind="task"))
    assert "  :PROPERTIES:" in out
    assert "  :ID: abc-123" in out
    assert "  :ORGTD_KIND: task" in out
    assert "  :END:" in out


def test_body_is_indented_under_headline():
    out = render(FakeNode(title="筆記", body="第一行\n第二行"))
    assert "\n  第一行\n  第二行" in out


def test_children_get_deeper_star_level():
    child = FakeNode(title="子任務", org_id="c1")
    parent = FakeNode(title="父專案", org_id="p1", kind="project")
    out = render(parent, [child])
    assert "* 父專案" in out
    assert "\n** 子任務" in out


# ---------------------------------------------------------------------------
# 從資料庫匯出
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(make_user):
    user_id, _ = make_user("exp@example.com")
    with SessionLocal() as session:
        tag = Tag(user_id=user_id, name="重要")
        session.add(tag)
        session.flush()
        inbox = Node(user_id=user_id, kind="inbox", title="待釐清的東西")
        note = Node(user_id=user_id, kind="note", title="一則筆記", body="內文")
        someday = Node(user_id=user_id, kind="someday", title="有天想學法文")
        project = Node(user_id=user_id, kind="project", title="搬家")
        gone = Node(
            user_id=user_id, kind="inbox", title="已封存的",
            archived_at=datetime.datetime.now(datetime.timezone.utc),
        )
        project.tags.append(tag)
        session.add_all([inbox, note, someday, project, gone])
        session.flush()
        session.add(Node(
            user_id=user_id, kind="task", title="找搬家公司",
            todo_state="NEXT", parent_id=project.id,
        ))
        session.commit()
        return user_id


def test_nodes_land_in_the_right_files(seeded):
    with SessionLocal() as session:
        files = orgfiles.build_export(session, seeded)

    assert "待釐清的東西" in files["inbox.org"]
    assert "一則筆記" in files["notes.org"]
    assert "有天想學法文" in files["someday.org"]
    assert "搬家" in files["projects.org"]
    # 任務跟著它的專案走，不會自己跑到別的檔案
    assert "找搬家公司" in files["projects.org"]
    assert "找搬家公司" not in files["inbox.org"]
    # 已封存的進 archive.org，不留在原本的檔案裡
    assert "已封存的" in files["archive.org"]
    assert "已封存的" not in files["inbox.org"]


def test_task_nested_under_its_project(seeded):
    with SessionLocal() as session:
        text = orgfiles.build_export(session, seeded)["projects.org"]
    assert "* 搬家" in text
    assert "** NEXT 找搬家公司" in text


def test_every_file_has_org_header(seeded):
    with SessionLocal() as session:
        files = orgfiles.build_export(session, seeded)
    for name, text in files.items():
        assert text.startswith("#+TITLE: "), name
        assert "#+TODO: TODO NEXT WAITING | DONE CANCELLED" in text, name


def test_org_id_is_stable_across_exports(seeded):
    """重複匯出必須給同一個 :ID:，否則使用者在 Emacs 建的連結會失效。"""
    with SessionLocal() as session:
        first = orgfiles.build_export(session, seeded)
    with SessionLocal() as session:
        second = orgfiles.build_export(session, seeded)
    assert first == second


def test_zip_contains_all_five_files(seeded):
    with SessionLocal() as session:
        data = orgfiles.make_zip(orgfiles.build_export(session, seeded))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == sorted(orgfiles.ORG_FILES)
        assert "待釐清的東西" in zf.read("inbox.org").decode("utf-8")


# ---------------------------------------------------------------------------
# HTTP 層
# ---------------------------------------------------------------------------


def test_download_requires_login(client):
    for path in ["/settings/export.zip", "/settings/export/inbox.org"]:
        resp = client.get(path)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


def test_zip_download_works(client, make_user):
    make_user("dl@example.com")
    login(client, "dl@example.com")
    resp = client.get("/settings/export.zip")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        assert sorted(zf.namelist()) == sorted(orgfiles.ORG_FILES)


def test_single_file_download_works(client, make_user):
    make_user("dl2@example.com")
    login(client, "dl2@example.com")
    token = csrf_from(client, "/")
    client.post("/inbox/capture", data={"title": "匯出測試", "csrf_token": token})

    resp = client.get("/settings/export/inbox.org")
    assert resp.status_code == 200
    assert "匯出測試" in resp.data.decode("utf-8")


def test_filename_must_be_on_the_whitelist(client, make_user):
    """檔名走白名單比對，不拿去拼路徑，所以沒有 ../ 穿越的餘地。"""
    make_user("dl3@example.com")
    login(client, "dl3@example.com")
    for bad in ["../../etc/passwd", "..%2fsecret", "secrets.org", "inbox.org.bak"]:
        assert client.get(f"/settings/export/{bad}").status_code == 404


def test_export_only_contains_own_data(client, make_user):
    a_id, _ = make_user("owner@example.com")
    make_user("other@example.com")
    with SessionLocal() as session:
        session.add(Node(user_id=a_id, kind="note", title="A 的私人筆記"))
        session.commit()

    login(client, "other@example.com")
    resp = client.get("/settings/export.zip")
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        assert "A 的私人筆記" not in zf.read("notes.org").decode("utf-8")
