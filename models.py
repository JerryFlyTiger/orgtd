"""GTD × org-mode 統一節點模型。

核心哲學（org-mode）：一切皆節點。Inbox 項目、專案、任務、筆記、
將來也許都存在同一張 nodes 表，用 kind 區分語意、parent_id 自關聯
構成 outline 大綱樹。Clarify 動作只是 UPDATE kind/parent_id/todo_state，
不用搬表，對應 GTD 流程的流動性。

多租戶：除 users 外，每張表都掛 user_id。所有查詢
一律以 user_id 起手，索引也把 user_id 放在最左欄，讓多租戶篩選能吃到索引。

org 檔對應：每個 node 有一組 (org_file, org_id)。org_id 寫進 org 檔的
:PROPERTIES: 抽屜（等同 Emacs org-id 的 :ID:），所以使用者在 Emacs 裡
搬動、改寫節點之後，仍然對得回同一列 DB 資料。
"""

import datetime
import uuid

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, Table, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def _uuid4() -> str:
    return str(uuid.uuid4())


class User(Base):
    """一個帳號。密碼可為 NULL —— 帳號已建立但還沒設定密碼，此時無法登入。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 對外識別碼。伺服器模式下同時是這個人的 org 資料夾名稱，
    # 用 UUID 而非流水號，避免從資料夾名稱推算出使用者總數。
    uuid: Mapped[str] = mapped_column(
        sa.String(36), unique=True, nullable=False, default=_uuid4
    )

    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(80), nullable=False)

    # argon2 雜湊。允許為 NULL：帳號可能先被建立（例如舊資料遷移過來的
    # 站長帳號）而還沒設密碼，此時不得以任何密碼登入，由
    # security.verify_password 擋住。
    password_hash: Mapped[str | None] = mapped_column(sa.String(255))

    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    # Flask-Login 介面（get_id 必須回傳字串）
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)


class RecoveryCode(Base):
    """一次性救援碼：忘記密碼時用來重設。

    為什麼不做「寄重設連結到信箱」：這個系統沒有寄信能力，而且自架環境
    要接 SMTP 等於多一組要保管的憑證與一個會壞的外部相依。救援碼把恢復
    能力交還給使用者自己保管，不需要任何外部服務。

    為什麼用 SHA-256 而不是 argon2：密碼要用慢雜湊，是因為人選的密碼熵很
    低，必須讓每次猜測都昂貴。救援碼是程式產生的 125 位元隨機值，暴力搜尋
    在物理上不可行，慢雜湊只會拖慢正常驗證。而且確定性雜湊可以直接用
    索引查到那一列；若用 argon2（每次加鹽），就得把使用者所有未使用的碼
    逐一驗過，10 組就是十次昂貴運算。
    """

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # sha256 十六進位，64 字元。全域唯一：碼本身就夠隨機，撞號視為異常。
    code_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    used_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now()
    )


Index("ix_recovery_user", RecoveryCode.user_id)


node_tags = Table(
    "node_tags",
    Base.metadata,
    sa.Column("node_id", ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Node(Base):
    """統一節點：outline 大綱樹的每一個 headline。"""

    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"))

    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="inbox")
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)

    # org TODO 狀態機：NULL=無狀態 / TODO / NEXT / WAITING / DONE / CANCELLED
    todo_state: Mapped[str | None] = mapped_column(sa.String(12))
    priority: Mapped[str | None] = mapped_column(sa.String(1))  # A/B/C

    # org 的 SCHEDULED（何時開始做）與 DEADLINE（截止），彼此獨立。
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    deadline_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # org 式重複規則（+1d/+1w/+1m）：完成時不關閉，而是把排程/截止日往後推。
    repeat_rule: Mapped[str | None] = mapped_column(sa.String(4))

    # 提醒：到點且尚未通知（notified_at 為 NULL）時由 notifier 發送。
    remind_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    notified_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    # 匯出成 org 檔時寫進 :PROPERTIES: 抽屜的 :ID:（等同 Emacs org-id）。
    # 存在資料庫而不是每次匯出臨時產生，是為了讓同一個節點在多次匯出之間
    # 拿到同一個值——否則使用者在 Emacs 裡建的 org-id 連結每匯出一次就失效。
    org_id: Mapped[str] = mapped_column(sa.String(36), nullable=False, default=_uuid4)

    archived_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    done_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # 全文搜尋向量，DB 端 generated column 自動維護，應用層不寫入。
    search_vec: Mapped[str | None] = mapped_column(
        TSVECTOR,
        sa.Computed(
            "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(body, ''))",
            persisted=True,
        ),
    )

    children: Mapped[list["Node"]] = relationship(
        "Node",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="Node.position",
    )
    parent: Mapped["Node | None"] = relationship(
        "Node", back_populates="children", remote_side=[id]
    )
    tags: Mapped[list["Tag"]] = relationship(
        "Tag", secondary=node_tags, back_populates="nodes"
    )
    pomodoros: Mapped[list["PomodoroSession"]] = relationship(
        "PomodoroSession", back_populates="node"
    )

    __table_args__ = (
        # org_id 只需在同一個使用者內唯一，跨使用者不必協調。
        UniqueConstraint("user_id", "org_id", name="uq_nodes_user_org_id"),
    )


# 多租戶索引原則：user_id 一律放最左，否則每次查詢都要全表掃再篩使用者。
Index("ix_nodes_user_parent", Node.user_id, Node.parent_id)
Index("ix_nodes_user_kind_state", Node.user_id, Node.kind, Node.todo_state)
# 部分索引：agenda 只查未封存節點，索引縮小、查詢更快。
Index(
    "ix_nodes_user_scheduled",
    Node.user_id,
    Node.scheduled_at,
    postgresql_where=Node.archived_at.is_(None),
)
Index(
    "ix_nodes_user_deadline",
    Node.user_id,
    Node.deadline_at,
    postgresql_where=Node.archived_at.is_(None),
)
Index(
    "ix_nodes_user_remind",
    Node.user_id,
    Node.remind_at,
    postgresql_where=sa.and_(Node.archived_at.is_(None), Node.notified_at.is_(None)),
)
Index("ix_nodes_search", Node.search_vec, postgresql_using="gin")
# 中文以 trigram 做子字串模糊搜尋（PostgreSQL 內建全文搜尋不斷中文詞）。
# GIN 不支援多欄混用 btree 欄位，所以 user_id 進不了這個索引，
# 由查詢端的 user_id = :uid 條件在 bitmap 階段再篩一次。
Index(
    "ix_nodes_title_trgm", Node.title, postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}
)
Index(
    "ix_nodes_body_trgm", Node.body, postgresql_using="gin", postgresql_ops={"body": "gin_trgm_ops"}
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    color: Mapped[str | None] = mapped_column(sa.String(7))  # #rrggbb

    nodes: Mapped[list["Node"]] = relationship(
        "Node", secondary=node_tags, back_populates="tags"
    )

    # 原本 name 是全域 unique，多人之後會讓 B 使用者無法建立 A 已用過的標籤。
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tags_user_name"),)


class PomodoroSession(Base):
    """一顆番茄 = org-clock 的一段計時，綁定任務節點。"""

    __tablename__ = "pomodoro_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="SET NULL"))

    started_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    planned_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=25)
    actual_seconds: Mapped[int | None] = mapped_column(sa.Integer)
    completed: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    kind: Mapped[str] = mapped_column(sa.String(12), default="focus")
    note: Mapped[str | None] = mapped_column(sa.Text)

    node: Mapped["Node | None"] = relationship("Node", back_populates="pomodoros")


Index("ix_pomo_user_node", PomodoroSession.user_id, PomodoroSession.node_id)
Index("ix_pomo_user_started", PomodoroSession.user_id, PomodoroSession.started_at)


class WeeklyReview(Base):
    """每週回顧一次一筆，checklist 用 JSONB 存 8 步完成狀態。"""

    __tablename__ = "weekly_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    week_start: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    checklist: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reflection: Mapped[str | None] = mapped_column(sa.Text)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now()
    )

    # 原本 week_start 是全域 unique —— 多人之後，第二個使用者做同一週的回顧
    # 會直接撞主鍵衝突。唯一性必須連同 user_id 一起判定。
    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_reviews_user_week"),
    )


class Setting(Base):
    """每人一列設定（番茄時長、每日目標等）JSONB，改設定免 migration。"""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 原本是「單人設定固定第 1 列」的設計，改為一人一列。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
