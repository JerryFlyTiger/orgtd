"""自訂例外，轉成友善繁中錯誤頁。"""


class NodeNotFoundError(Exception):
    """指定的節點不存在或已被封存。"""

    def __init__(self, node_id):
        super().__init__(f"找不到節點 id={node_id}")
        self.node_id = node_id
