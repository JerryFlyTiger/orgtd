"""org 檔匯出：把資料庫裡的節點輸出成 org-mode 純文字。

這是**單向匯出**，不是同步。資料庫是唯一的事實來源，org 檔是使用者
想帶走時才產生的快照——按下匯出就即時算出來，伺服器上不留檔案。

刻意不做「把 org 檔改動讀回資料庫」：那需要在磁碟上長期保管每個使用者
的資料夾、偵測外部改動、處理兩邊同時修改的衝突，複雜度遠高於它帶來的
價值。要在 Emacs 或 VSCode 裡編輯的人，下載一份帶走即可。

匯出格式是標準 org-mode，Emacs 的 org-agenda 直接吃得到，
VSCode 裝 Org Mode 擴充套件也能用；沒裝擴充當純文字讀也不會亂。
"""

from __future__ import annotations

import datetime
import io
import zipfile

from sqlalchemy import select

# 檔案配置沿用 org-mode GTD 社群慣例，讓習慣這套的人一眼認得。
FILE_FOR_KIND = {
    "inbox": "inbox.org",
    "project": "projects.org",
    "task": "projects.org",  # 任務掛在專案底下，跟著專案走
    "note": "notes.org",
    "someday": "someday.org",
}
ARCHIVE_FILE = "archive.org"
ORG_FILES = ["inbox.org", "projects.org", "notes.org", "someday.org", ARCHIVE_FILE]

FILE_TITLES = {
    "inbox.org": "收集箱",
    "projects.org": "專案",
    "notes.org": "筆記",
    "someday.org": "將來也許",
    ARCHIVE_FILE: "封存",
}

# ---------------------------------------------------------------------------
# org 時間戳
# ---------------------------------------------------------------------------

_DAYNAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def format_timestamp(dt: datetime.datetime | None, repeat: str | None = None) -> str:
    """輸出 <2026-09-08 Tue 09:00 +1w> 形式。

    星期一律寫英文縮寫。Emacs 讀取時不在意這欄的語言，但英文縮寫在任何
    語系下都解析得了，可攜性最好。
    """
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # org 時間戳是本地牆上時間，沒有時區欄位
    core = f"{dt:%Y-%m-%d} {_DAYNAMES[dt.weekday()]}"
    if (dt.hour, dt.minute) != (0, 0):
        core += f" {dt:%H:%M}"
    if repeat:
        core += f" {repeat}"
    return f"<{core}>"


# ---------------------------------------------------------------------------
# 節點 → org 文字
# ---------------------------------------------------------------------------


def file_header(filename: str) -> str:
    title = FILE_TITLES.get(filename, filename)
    return (
        f"#+TITLE: {title}\n"
        f"#+TODO: TODO NEXT WAITING | DONE CANCELLED\n"
        f"#+STARTUP: overview\n\n"
    )


def render_node(node, depth: int = 1, children_of=None) -> str:
    """輸出一個節點（含子樹）。

    children_of(node) 回傳子節點串列，由呼叫端決定怎麼取，
    這個模組不直接碰資料庫查詢。
    """
    out = [_headline(node, depth)]

    planning = _planning(node)
    if planning:
        out.append(planning)

    out.append(_properties(node))

    body = (getattr(node, "body", None) or "").strip()
    if body:
        indent = " " * (depth + 1)
        out.extend(f"{indent}{ln}" if ln.strip() else "" for ln in body.split("\n"))

    out.append("")

    if children_of is not None:
        for child in children_of(node):
            out.append(render_node(child, depth + 1, children_of))

    return "\n".join(out)


def _headline(node, depth: int) -> str:
    parts = ["*" * depth]
    if getattr(node, "todo_state", None):
        parts.append(node.todo_state)
    if getattr(node, "priority", None):
        parts.append(f"[#{node.priority}]")
    # 標題內的換行會把一個 headline 拆成兩行，破壞整份檔案的結構。
    parts.append((node.title or "").replace("\n", " "))
    line = " ".join(parts)

    tags = [t.name for t in getattr(node, "tags", []) or []]
    if tags:
        # org 慣例是標籤靠右對齊到第 77 欄；欄寬不夠就退回單一空格。
        tag_str = ":" + ":".join(tags) + ":"
        line += " " * max(1, 77 - len(line) - len(tag_str)) + tag_str
    return line


def _planning(node) -> str:
    bits = []
    if getattr(node, "scheduled_at", None):
        bits.append(f"SCHEDULED: {format_timestamp(node.scheduled_at, node.repeat_rule)}")
    if getattr(node, "deadline_at", None):
        bits.append(f"DEADLINE: {format_timestamp(node.deadline_at)}")
    return ("  " + " ".join(bits)) if bits else ""


def _properties(node) -> str:
    """PROPERTIES 抽屜。

    :ID: 用的是 Emacs 內建 org-id 的欄位名，且每次匯出對同一個節點都給
    同一個值——所以重複匯出時，使用者在 Emacs 裡建立的 org-id 連結不會失效。
    """
    props = [(":ID:", node.org_id)]
    if getattr(node, "kind", None):
        props.append((":ORGTD_KIND:", node.kind))
    if getattr(node, "remind_at", None):
        props.append((":ORGTD_REMIND:", format_timestamp(node.remind_at)))
    lines = ["  :PROPERTIES:"]
    lines += [f"  {k} {v}" for k, v in props if v]
    lines.append("  :END:")
    return "\n".join(lines)


def render_file(filename: str, roots, children_of) -> str:
    return file_header(filename) + "".join(
        render_node(n, 1, children_of) for n in roots
    )


# ---------------------------------------------------------------------------
# 匯出
# ---------------------------------------------------------------------------


def build_export(session, user_id: int) -> dict[str, str]:
    """把這位使用者的所有節點算成 {檔名: org 文字}。全部在記憶體完成。"""
    from models import Node

    def children_of(node):
        return session.scalars(
            select(Node)
            .where(Node.user_id == user_id, Node.parent_id == node.id)
            .order_by(Node.position, Node.id)
        ).all()

    files = {}
    for filename in ORG_FILES:
        stmt = (
            select(Node)
            .where(Node.user_id == user_id, Node.parent_id.is_(None))
            .order_by(Node.position, Node.id)
        )
        if filename == ARCHIVE_FILE:
            stmt = stmt.where(Node.archived_at.is_not(None))
        else:
            kinds = [k for k, f in FILE_FOR_KIND.items() if f == filename]
            stmt = stmt.where(Node.archived_at.is_(None), Node.kind.in_(kinds))

        files[filename] = render_file(filename, session.scalars(stmt).all(), children_of)
    return files


def count_headlines(text: str) -> int:
    return sum(1 for ln in text.split("\n") if ln.startswith("*"))


def make_zip(files: dict[str, str]) -> bytes:
    """打包成 zip bytes，直接串給瀏覽器下載，不落地。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buffer.getvalue()
