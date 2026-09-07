"""DB ⇄ org 檔的同步引擎。

方向與時機：

* 網頁端改動 → 立刻重寫該節點所屬的整個 org 檔（檔案小，整檔重寫遠比
  就地修補可靠，也不會累積格式漂移）。
* 外部（Emacs / VSCode）改動 → 進站時比對檔案指紋，有變就重新解析、
  回寫 DB。以 :ID: 屬性對回原本那列，所以搬動與改標題都不會斷掉番茄鐘
  紀錄與提醒設定。

衝突處理採「檔案優先」：如果一個檔案自上次同步後在外部被改過，就以檔案
內容為準覆蓋 DB。這與 orgfiles.py 開頭宣告的事實來源一致——使用者在
Emacs 裡打的字不該被網頁悄悄蓋掉。
"""

from __future__ import annotations

import datetime
import pathlib

from sqlalchemy import select

import orgfiles
from models import Node, Tag

# 指紋的讀寫實作在 orgfiles，因為建立資料夾時就得登記，那裡是檔案層的家。
_load_state = orgfiles.load_state
_save_state = orgfiles.save_state
_digest = orgfiles.digest


def _write_if_changed(path: pathlib.Path, text: str, state: dict) -> bool:
    """內容沒變就不寫檔——避免每次瀏覽都更新 mtime，讓外部改動偵測失準。"""
    d = _digest(text)
    if state.get(path.name, {}).get("digest") == d and path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    state[path.name] = {"digest": d, "mtime": path.stat().st_mtime}
    return True


# ---------------------------------------------------------------------------
# 節點 → 檔案歸屬
# ---------------------------------------------------------------------------


def _root_of(session, node: Node) -> Node:
    seen = {node.id}
    current = node
    while current.parent_id is not None:
        parent = session.get(Node, current.parent_id)
        if parent is None or parent.id in seen:
            break  # 資料異常時不要無限迴圈
        seen.add(parent.id)
        current = parent
    return current


def file_for(session, node: Node) -> str:
    if node.archived_at is not None:
        return orgfiles.ARCHIVE_FILE
    root = _root_of(session, node)
    return orgfiles.FILE_FOR_KIND.get(root.kind, "inbox.org")


# ---------------------------------------------------------------------------
# 匯出：DB → org 檔
# ---------------------------------------------------------------------------


def _children_of(session, user_id):
    def inner(node):
        return session.scalars(
            select(Node)
            .where(Node.user_id == user_id, Node.parent_id == node.id)
            .order_by(Node.position, Node.id)
        ).all()

    return inner


def rebuild_file(session, user, filename: str, state: dict | None = None) -> bool:
    """依 DB 現況重寫單一 org 檔。"""
    root_dir = orgfiles.user_org_dir(user)
    own_state = state is None
    state = _load_state(root_dir) if own_state else state

    archived = filename == orgfiles.ARCHIVE_FILE
    kinds = [k for k, f in orgfiles.FILE_FOR_KIND.items() if f == filename]

    stmt = select(Node).where(
        Node.user_id == user.id, Node.parent_id.is_(None)
    ).order_by(Node.position, Node.id)
    if archived:
        stmt = stmt.where(Node.archived_at.is_not(None))
    else:
        stmt = stmt.where(Node.archived_at.is_(None), Node.kind.in_(kinds))

    roots = session.scalars(stmt).all()
    text = orgfiles.render_file(filename, roots, _children_of(session, user.id))

    changed = _write_if_changed(root_dir / filename, text, state)
    for node in roots:
        if node.org_file != filename:
            node.org_file = filename
    session.commit()

    if own_state:
        _save_state(root_dir, state)
    return changed


def rebuild_all(session, user) -> None:
    root_dir = orgfiles.provision_user_directory(user)
    state = _load_state(root_dir)
    for name in orgfiles.ORG_FILES:
        rebuild_file(session, user, name, state)
    _save_state(root_dir, state)


def sync_node(session, node: Node) -> None:
    """單一節點變更後重寫它所屬的檔案。view 在 commit 之後呼叫。

    org 資料夾不存在或無法寫入時不讓整個請求失敗——網頁功能不該被
    檔案系統問題拖垮，錯誤留給設定頁的健康檢查顯示。
    """
    from models import User

    user = session.get(User, node.user_id)
    if user is None:
        return
    try:
        rebuild_file(session, user, file_for(session, node))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 匯入：org 檔 → DB
# ---------------------------------------------------------------------------


def externally_changed(user) -> list[str]:
    """回傳自上次同步後在外部被改過的檔名。"""
    root_dir = orgfiles.user_org_dir(user)
    if not root_dir.exists():
        return []
    state = _load_state(root_dir)
    changed = []
    for name in orgfiles.ORG_FILES:
        path = root_dir / name
        if not path.exists():
            continue
        recorded = state.get(name, {}).get("digest")
        if recorded != _digest(path.read_text(encoding="utf-8")):
            changed.append(name)
    return changed


def import_file(session, user, filename: str) -> dict:
    """解析一個 org 檔並回寫 DB，回傳異動統計。"""
    root_dir = orgfiles.user_org_dir(user)
    path = root_dir / filename
    if not path.exists():
        return {"created": 0, "updated": 0, "archived": 0}

    text = path.read_text(encoding="utf-8")
    parsed = orgfiles.ensure_ids(orgfiles.parse_file(text))

    existing = {
        n.org_id: n
        for n in session.scalars(
            select(Node).where(Node.user_id == user.id, Node.org_file == filename)
        ).all()
    }

    stats = {"created": 0, "updated": 0, "archived": 0}
    seen: set[str] = set()

    def walk(items, parent_id, depth_kind):
        for pos, item in enumerate(items):
            seen.add(item["org_id"])
            node = existing.get(item["org_id"])
            kind = item["kind"] or depth_kind
            if node is None:
                node = Node(user_id=user.id, org_id=item["org_id"], title=item["title"])
                session.add(node)
                stats["created"] += 1
            else:
                stats["updated"] += 1
            node.title = item["title"] or "(未命名)"
            node.body = item["body"]
            node.todo_state = item["todo_state"]
            node.priority = item["priority"]
            node.scheduled_at = item["scheduled_at"]
            node.deadline_at = item["deadline_at"]
            node.repeat_rule = item["repeat_rule"]
            if item["remind_at"] is not None:
                node.remind_at = item["remind_at"]
            node.kind = kind
            node.parent_id = parent_id
            node.position = pos
            node.org_file = filename
            node.archived_at = (
                datetime.datetime.now(datetime.timezone.utc)
                if filename == orgfiles.ARCHIVE_FILE
                else None
            )
            _apply_tags(session, user, node, item["tags"])
            session.flush()
            # 專案底下的子節點預設是任務，其餘沿用父層語意。
            walk(item["children"], node.id, "task" if kind == "project" else kind)

    default_kind = next(
        (k for k, f in orgfiles.FILE_FOR_KIND.items() if f == filename), "inbox"
    )
    walk(parsed, None, default_kind)

    # 檔案裡消失的節點視為已刪除 → 封存而非硬刪，避免誤刪無法救回。
    for org_id, node in existing.items():
        if org_id not in seen and node.archived_at is None:
            node.archived_at = datetime.datetime.now(datetime.timezone.utc)
            stats["archived"] += 1

    session.commit()

    # 匯入後把指紋更新成目前檔案內容，否則下次又會被判定成外部改動。
    state = _load_state(root_dir)
    state[filename] = {"digest": _digest(text), "mtime": path.stat().st_mtime}
    _save_state(root_dir, state)
    return stats


def _apply_tags(session, user, node: Node, names: list[str]) -> None:
    wanted = set(names or [])
    current = {t.name for t in node.tags}
    for name in wanted - current:
        tag = session.scalar(
            select(Tag).where(Tag.user_id == user.id, Tag.name == name)
        )
        if tag is None:
            tag = Tag(user_id=user.id, name=name)
            session.add(tag)
            session.flush()
        node.tags.append(tag)
    for tag in [t for t in node.tags if t.name not in wanted]:
        node.tags.remove(tag)


def import_changed(session, user) -> dict:
    """把所有外部改動過的檔案吃回 DB。"""
    total = {"created": 0, "updated": 0, "archived": 0, "files": []}
    for name in externally_changed(user):
        stats = import_file(session, user, name)
        total["files"].append(name)
        for k in ("created", "updated", "archived"):
            total[k] += stats[k]
    return total
