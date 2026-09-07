"""org 檔存放層：DB 節點 ⇄ .org 純文字的雙向轉換與檔案管理。

設計取捨——**org 檔是事實來源，PostgreSQL 是衍生索引**。

理由：需求是「這些檔案也要能用 Emacs / VSCode 打開編輯」。如果以 DB 為準、
org 檔只是匯出，那麼使用者在 Emacs 裡的修改下一次同步就會被覆蓋掉，
「能用 Emacs 編輯」就是假的。反過來以檔案為準，Postgres 仍然保有它真正
擅長的事：遞迴大綱查詢、pg_trgm 中文模糊搜尋、agenda 的部分索引、
番茄鐘統計——這些都是唯讀路徑，不受影響。

節點身分靠 :PROPERTIES: 抽屜裡的 :ID:（等同 Emacs 內建 org-id 的用法），
所以使用者在 Emacs 裡搬動、重排、改寫標題之後，仍然對得回同一列 DB 資料，
番茄鐘記錄與提醒設定不會斷。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import re
import shutil
import uuid

import config

# 每位使用者資料夾內的標準檔案配置，沿用 org-mode GTD 社群慣例的檔名。
FILE_FOR_KIND = {
    "inbox": "inbox.org",
    "project": "projects.org",
    "task": "projects.org",  # 任務掛在專案底下，跟著專案走
    "note": "notes.org",
    "someday": "someday.org",
}
ARCHIVE_FILE = "archive.org"
ORG_FILES = ["inbox.org", "projects.org", "notes.org", "someday.org", ARCHIVE_FILE]

# 應用內部狀態（同步指紋等），放在使用者資料夾內但以點開頭，Emacs 不會誤收。
STATE_DIR = ".orgtd"

_TITLE_FOR_FILE = {
    "inbox.org": "收集箱",
    "projects.org": "專案",
    "notes.org": "筆記",
    "someday.org": "將來也許",
    ARCHIVE_FILE: "封存",
}

TODO_STATES = ("TODO", "NEXT", "WAITING", "DONE", "CANCELLED")

# ---------------------------------------------------------------------------
# 目錄管理
# ---------------------------------------------------------------------------


def user_org_dir(user) -> pathlib.Path:
    """這個使用者的 org 資料夾。

    自架單人模式（ORGTD_ALLOW_CUSTOM_ORG_DIR=1）下使用者可自行指定路徑；
    對外站台一律用受管目錄 <ORG_ROOT>/<uuid>/，不接受使用者輸入的路徑，
    否則等同開放任意寫入伺服器檔案系統。
    """
    if config.ALLOW_CUSTOM_ORG_DIR and user.org_directory:
        return pathlib.Path(user.org_directory).expanduser().resolve()
    return config.ORG_ROOT / user.uuid


STATE_FILE = "state.json"


def state_path(root: pathlib.Path) -> pathlib.Path:
    return root / STATE_DIR / STATE_FILE


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state(root: pathlib.Path) -> dict:
    path = state_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(root: pathlib.Path, state: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def provision_user_directory(user) -> pathlib.Path:
    """建立資料夾與五個標準 org 檔（已存在則不動）。

    新建的檔案要一併登記指紋，否則下一次 externally_changed() 會把這些
    我們自己剛寫出來的空檔案判定成「使用者在外部改過」，設定頁上每個
    新帳號一開始就會掛滿「外部已修改」的假警報。
    """
    root = user_org_dir(user)
    root.mkdir(parents=True, exist_ok=True)
    (root / STATE_DIR).mkdir(exist_ok=True)
    state = load_state(root)
    dirty = False
    for name in ORG_FILES:
        path = root / name
        if not path.exists():
            text = _file_header(name)
            path.write_text(text, encoding="utf-8")
            state[name] = {"digest": digest(text), "mtime": path.stat().st_mtime}
            dirty = True
    if dirty:
        save_state(root, state)
    return root


def _file_header(name: str) -> str:
    title = _TITLE_FOR_FILE.get(name, name)
    return (
        f"#+TITLE: {title}\n"
        f"#+TODO: TODO NEXT WAITING | DONE CANCELLED\n"
        f"#+STARTUP: overview\n\n"
    )


def validate_custom_dir(raw: str) -> tuple[pathlib.Path | None, str | None]:
    """檢查使用者輸入的自訂路徑，回傳 (path, 錯誤訊息)。"""
    if not raw or not raw.strip():
        return None, "路徑不可空白。"
    path = pathlib.Path(raw.strip()).expanduser()
    if not path.is_absolute():
        return None, "請填絕對路徑，例如 ~/org 或 /Users/你/org。"
    try:
        path = path.resolve()
    except OSError as e:
        return None, f"無法解析路徑：{e}"
    # 採正面表列：必須落在家目錄「之內」，且不是家目錄本身。
    #
    # 早先寫成黑名單（擋 /etc、/usr…）有兩個問題：macOS 的 /etc、/var 都是
    # 指向 /private/... 的符號連結，resolve() 之後字面前綴比對就被繞過；
    # 而把 /private 整個擋掉又會誤傷 /var/folders 這類正常路徑。
    # 自架情境下 org 資料夾本來就該放家目錄，正面表列既簡單又不留縫。
    home = pathlib.Path.home().resolve()
    if path == home:
        return None, "不接受家目錄本身，請指定一個專屬子資料夾，例如 ~/org。"
    if not path.is_relative_to(home):
        return None, f"路徑必須在家目錄（{home}）底下。"
    return path, None


def dir_size_bytes(root: pathlib.Path) -> int:
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


# ---------------------------------------------------------------------------
# org 時間戳
# ---------------------------------------------------------------------------

_DAYNAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# <2026-09-08 Tue 09:00 +1w> —— 星期欄允許任何非空白字串（Emacs 會依語系
# 寫成「週二」之類），解析時直接略過，只認日期、時間與重複規則。
_TS_RE = re.compile(
    r"[<\[](\d{4})-(\d{2})-(\d{2})(?:\s+\S+)?"
    r"(?:\s+(\d{2}):(\d{2}))?"
    r"(?:\s+([+.]{1,2}\d+[dwmy]))?"
    r"[>\]]"
)


def format_timestamp(dt: datetime.datetime | None, repeat: str | None = None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # org 時間戳是本地牆上時間，沒有時區欄位
    day = _DAYNAMES[dt.weekday()]
    core = f"{dt:%Y-%m-%d} {day}"
    if (dt.hour, dt.minute) != (0, 0):
        core += f" {dt:%H:%M}"
    if repeat:
        core += f" {repeat}"
    return f"<{core}>"


def parse_timestamp(text: str) -> tuple[datetime.datetime | None, str | None]:
    m = _TS_RE.search(text or "")
    if not m:
        return None, None
    y, mo, d, hh, mm, rep = m.groups()
    dt = datetime.datetime(
        int(y), int(mo), int(d), int(hh or 0), int(mm or 0)
    ).astimezone()
    return dt, rep


# ---------------------------------------------------------------------------
# 輸出：節點 → org 文字
# ---------------------------------------------------------------------------


def render_node(node, depth: int = 1, children_of=None) -> str:
    """把一個節點（含子樹）輸出成 org 文字。

    children_of(node) 回傳該節點的子節點串列，讓呼叫端決定子樹怎麼取
    （ORM relationship 或預先撈好的字典），這個模組不碰資料庫。
    """
    out = [_render_headline(node, depth)]

    planning = _render_planning(node)
    if planning:
        out.append(planning)

    out.append(_render_properties(node))

    body = (getattr(node, "body", None) or "").strip()
    if body:
        indent = " " * (depth + 1)
        out.extend(f"{indent}{line}" if line.strip() else "" for line in body.split("\n"))

    out.append("")

    if children_of is not None:
        for child in children_of(node):
            out.append(render_node(child, depth + 1, children_of))

    return "\n".join(out)


def _render_headline(node, depth: int) -> str:
    parts = ["*" * depth]
    if getattr(node, "todo_state", None):
        parts.append(node.todo_state)
    if getattr(node, "priority", None):
        parts.append(f"[#{node.priority}]")
    parts.append((node.title or "").replace("\n", " "))
    line = " ".join(parts)

    tags = [t.name for t in getattr(node, "tags", []) or []]
    if tags:
        # org 慣例是把標籤靠右對齊到第 77 欄，欄寬不足就退回單一空格。
        tag_str = ":" + ":".join(tags) + ":"
        pad = max(1, 77 - len(line) - len(tag_str))
        line = line + " " * pad + tag_str
    return line


def _render_planning(node) -> str:
    bits = []
    if getattr(node, "scheduled_at", None):
        bits.append(f"SCHEDULED: {format_timestamp(node.scheduled_at, node.repeat_rule)}")
    if getattr(node, "deadline_at", None):
        bits.append(f"DEADLINE: {format_timestamp(node.deadline_at)}")
    return ("  " + " ".join(bits)) if bits else ""


def _render_properties(node) -> str:
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
    body = "".join(render_node(n, 1, children_of) for n in roots)
    return _file_header(filename) + body


# ---------------------------------------------------------------------------
# 解析：org 文字 → 節點字典
# ---------------------------------------------------------------------------

_HEADLINE_RE = re.compile(r"^(\*+)\s+(.*)$")
_TAGS_RE = re.compile(r"\s+(:(?:[\w@#%]+:)+)\s*$")
_PRIORITY_RE = re.compile(r"^\[#([ABC])\]\s*")
_PROP_RE = re.compile(r"^\s*:([A-Za-z_][\w-]*):\s*(.*)$")


def parse_file(text: str) -> list[dict]:
    """把 org 文字解析成巢狀節點字典。

    無法辨識的內容一律保留在最近一個節點的 body 裡，不丟棄——使用者在
    Emacs 裡加的東西如果會被靜默吃掉，這套同步就不可信。
    """
    roots: list[dict] = []
    stack: list[dict] = []
    current: dict | None = None
    in_props = False

    for raw in (text or "").split("\n"):
        m = _HEADLINE_RE.match(raw)
        if m:
            in_props = False
            depth = len(m.group(1))
            node = _parse_headline(m.group(2), depth)
            while stack and stack[-1]["depth"] >= depth:
                stack.pop()
            if stack:
                stack[-1]["children"].append(node)
            else:
                roots.append(node)
            stack.append(node)
            current = node
            continue

        if current is None:
            continue  # 檔頭 #+TITLE: 之類，不屬於任何節點

        line = raw.strip()

        if line == ":PROPERTIES:":
            in_props = True
            continue
        if line == ":END:":
            in_props = False
            continue
        if in_props:
            pm = _PROP_RE.match(raw)
            if pm:
                _apply_property(current, pm.group(1).upper(), pm.group(2).strip())
            continue

        if line.startswith(("SCHEDULED:", "DEADLINE:")) or (
            "SCHEDULED:" in line and "DEADLINE:" in line
        ):
            _apply_planning(current, line)
            continue

        current["body_lines"].append(raw.strip())

    for node in _walk(roots):
        node["body"] = "\n".join(node.pop("body_lines")).strip() or None
    return roots


def _parse_headline(rest: str, depth: int) -> dict:
    tags: list[str] = []
    tm = _TAGS_RE.search(rest)
    if tm:
        tags = [t for t in tm.group(1).strip(":").split(":") if t]
        rest = rest[: tm.start()].rstrip()

    todo_state = None
    for state in TODO_STATES:
        if rest == state or rest.startswith(state + " "):
            todo_state = state
            rest = rest[len(state) :].lstrip()
            break

    priority = None
    pm = _PRIORITY_RE.match(rest)
    if pm:
        priority = pm.group(1)
        rest = rest[pm.end() :]

    return {
        "depth": depth,
        "title": rest.strip(),
        "todo_state": todo_state,
        "priority": priority,
        "tags": tags,
        "org_id": None,
        "kind": None,
        "scheduled_at": None,
        "deadline_at": None,
        "remind_at": None,
        "repeat_rule": None,
        "body_lines": [],
        "children": [],
    }


def _apply_property(node: dict, key: str, value: str) -> None:
    if key == "ID":
        node["org_id"] = value
    elif key == "ORGTD_KIND":
        node["kind"] = value
    elif key == "ORGTD_REMIND":
        node["remind_at"], _ = parse_timestamp(value)


def _apply_planning(node: dict, line: str) -> None:
    # 一行可能同時有 SCHEDULED 與 DEADLINE，各自取自己後面那個時間戳。
    for key, field in (("SCHEDULED:", "scheduled_at"), ("DEADLINE:", "deadline_at")):
        idx = line.find(key)
        if idx == -1:
            continue
        dt, rep = parse_timestamp(line[idx + len(key) :])
        if dt:
            node[field] = dt
            if field == "scheduled_at" and rep:
                node["repeat_rule"] = rep


def _walk(nodes: list[dict]):
    for n in nodes:
        yield n
        yield from _walk(n["children"])


def ensure_ids(nodes: list[dict]) -> list[dict]:
    """替使用者在 Emacs 裡新增、還沒有 :ID: 的節點補上識別碼。"""
    for node in _walk(nodes):
        if not node["org_id"]:
            node["org_id"] = str(uuid.uuid4())
    return nodes


# ---------------------------------------------------------------------------
# 匯出
# ---------------------------------------------------------------------------


def make_zip(root: pathlib.Path, dest_dir: pathlib.Path, stem: str) -> pathlib.Path:
    """打包整個資料夾，供使用者下載到自己電腦用 Emacs / VSCode 開。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(
        shutil.make_archive(str(dest_dir / stem), "zip", root_dir=str(root))
    )
