"""org 檔解析／輸出的往返正確性。

這層若有損耗，使用者在 Emacs 裡的編輯就會被靜默吃掉，
是整個「檔案為事實來源」設計的地基。
"""

import datetime

import orgfiles


class FakeTag:
    def __init__(self, name):
        self.name = name


class FakeNode:
    def __init__(self, **kw):
        defaults = dict(
            title="", body=None, todo_state=None, priority=None, tags=[],
            scheduled_at=None, deadline_at=None, remind_at=None,
            repeat_rule=None, org_id="x", kind="task",
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


def render_one(node, children=()):
    kids = {id(node): list(children)}
    for c in children:
        kids.setdefault(id(c), [])
    return orgfiles.render_file("projects.org", [node], lambda n: kids[id(n)])


def test_headline_states_and_priority():
    for state in ("TODO", "NEXT", "WAITING", "DONE", "CANCELLED"):
        node = FakeNode(title="事情", todo_state=state, priority="A", org_id="i1")
        parsed = orgfiles.parse_file(render_one(node))[0]
        assert parsed["todo_state"] == state
        assert parsed["priority"] == "A"
        assert parsed["title"] == "事情"


def test_title_without_state_is_not_swallowed():
    # 標題開頭剛好是狀態字樣的一部分時，不可被誤判成 TODO 狀態
    node = FakeNode(title="TODOs 清單整理", org_id="i2")
    parsed = orgfiles.parse_file(render_one(node))[0]
    assert parsed["todo_state"] is None
    assert parsed["title"] == "TODOs 清單整理"


def test_chinese_title_and_tags():
    node = FakeNode(
        title="買菜與晚餐準備", org_id="i3", tags=[FakeTag("家事"), FakeTag("errand")]
    )
    parsed = orgfiles.parse_file(render_one(node))[0]
    assert parsed["title"] == "買菜與晚餐準備"
    assert sorted(parsed["tags"]) == sorted(["家事", "errand"])


def test_scheduled_deadline_and_repeat():
    sched = datetime.datetime(2026, 3, 2, 14, 30).astimezone()
    dead = datetime.datetime(2026, 3, 9).astimezone()
    node = FakeNode(
        title="週會", org_id="i4", scheduled_at=sched, deadline_at=dead, repeat_rule="+1w"
    )
    parsed = orgfiles.parse_file(render_one(node))[0]
    assert parsed["scheduled_at"].strftime("%Y-%m-%d %H:%M") == "2026-03-02 14:30"
    assert parsed["deadline_at"].strftime("%Y-%m-%d") == "2026-03-09"
    assert parsed["repeat_rule"] == "+1w"


def test_multiline_body_preserved():
    node = FakeNode(title="筆記", org_id="i5", body="第一行\n第二行\n第三行")
    parsed = orgfiles.parse_file(render_one(node))[0]
    assert parsed["body"] == "第一行\n第二行\n第三行"


def test_nested_children_depth():
    child = FakeNode(title="子任務", org_id="c1", kind="task")
    parent = FakeNode(title="父專案", org_id="p1", kind="project")
    parsed = orgfiles.parse_file(render_one(parent, [child]))
    assert len(parsed) == 1
    assert parsed[0]["children"][0]["title"] == "子任務"
    assert parsed[0]["children"][0]["org_id"] == "c1"


def test_org_id_survives_manual_title_edit():
    """使用者在 Emacs 裡改標題後，:ID: 不變 —— 這是能對回同一列的關鍵。"""
    node = FakeNode(title="原標題", org_id="stable-id")
    text = render_one(node)
    edited = text.replace("原標題", "使用者改過的標題")
    parsed = orgfiles.parse_file(edited)[0]
    assert parsed["org_id"] == "stable-id"
    assert parsed["title"] == "使用者改過的標題"


def test_emacs_style_localised_daynames_parse():
    """Emacs 依語系會把星期寫成「週一」，解析時必須照樣讀得到日期。"""
    dt, rep = orgfiles.parse_timestamp("<2026-03-02 週一 14:30 +1d>")
    assert dt.strftime("%Y-%m-%d %H:%M") == "2026-03-02 14:30"
    assert rep == "+1d"


def test_ensure_ids_fills_manually_added_nodes():
    """使用者在 Emacs 手打的新節點沒有 :ID:，匯入前必須自動補上。"""
    text = "#+TITLE: x\n\n* TODO 手動加的項目\n"
    parsed = orgfiles.ensure_ids(orgfiles.parse_file(text))
    assert parsed[0]["org_id"]
    assert len(parsed[0]["org_id"]) == 36
