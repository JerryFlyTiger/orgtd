"""多租戶取用輔助：所有「用 id 取單一物件」的路徑都必須走這裡。

原本是單人系統，view 裡直接 session.get(Node, node_id) 取主鍵即可。
一旦多人共用，那就是典型的 IDOR（Insecure Direct Object Reference）——
把網址上的 id 換成別人的節點編號就能讀寫別人的資料。
"""

from flask import abort
from flask_login import current_user
from sqlalchemy import select

from models import Node


def uid() -> int:
    """目前登入者的 id。未登入時 @login_required 早已擋下，不會走到這裡。"""
    return current_user.id


def owned_node(session, node_id, *, kind=None, allow_archived=True) -> Node:
    """取一個屬於目前使用者的節點，否則 404。

    「不存在」與「存在但不屬於你」刻意回傳同一種 404：若兩者訊息不同，
    攻擊者可以靠回應差異枚舉出哪些 id 是有效的。
    """
    if node_id is None:
        abort(404)
    node = session.scalar(
        select(Node).where(Node.id == node_id, Node.user_id == current_user.id)
    )
    if node is None:
        abort(404)
    if kind is not None and node.kind != kind:
        abort(404)
    if not allow_archived and node.archived_at is not None:
        abort(404)
    return node
