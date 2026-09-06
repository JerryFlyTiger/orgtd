"""GTD × org-mode 統一節點模型。

核心哲學（org-mode）：一切皆節點。Inbox 項目、專案、任務、筆記、
將來也許都存在同一張 nodes 表，用 kind 區分語意、parent_id 自關聯
構成 outline 大綱樹。Clarify 動作只是 UPDATE kind/parent_id/todo_state，
不用搬表，對應 GTD 流程的流動性。
"""

import datetime

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, Table, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

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


Index("ix_nodes_parent", Node.parent_id)
Index("ix_nodes_kind_state", Node.kind, Node.todo_state)
# 部分索引：agenda 只查未封存節點，索引縮小、查詢更快。
Index(
    "ix_nodes_scheduled",
    Node.scheduled_at,
    postgresql_where=Node.archived_at.is_(None),
)
Index(
    "ix_nodes_deadline",
    Node.deadline_at,
    postgresql_where=Node.archived_at.is_(None),
)
Index(
    "ix_nodes_remind",
    Node.remind_at,
    postgresql_where=sa.and_(Node.archived_at.is_(None), Node.notified_at.is_(None)),
)
Index("ix_nodes_search", Node.search_vec, postgresql_using="gin")
# 中文以 trigram 做子字串模糊搜尋（PostgreSQL 內建全文搜尋不斷中文詞）。
Index(
    "ix_nodes_title_trgm", Node.title, postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}
)
Index(
    "ix_nodes_body_trgm", Node.body, postgresql_using="gin", postgresql_ops={"body": "gin_trgm_ops"}
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(50), unique=True, nullable=False)
    color: Mapped[str | None] = mapped_column(sa.String(7))  # #rrggbb

    nodes: Mapped[list["Node"]] = relationship(
        "Node", secondary=node_tags, back_populates="tags"
    )


class PomodoroSession(Base):
    """一顆番茄 = org-clock 的一段計時，綁定任務節點。"""

    __tablename__ = "pomodoro_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
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


Index("ix_pomo_node", PomodoroSession.node_id)
Index("ix_pomo_started", PomodoroSession.started_at)


class WeeklyReview(Base):
    """每週回顧一次一筆，checklist 用 JSONB 存 8 步完成狀態。"""

    __tablename__ = "weekly_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False, unique=True)
    checklist: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reflection: Mapped[str | None] = mapped_column(sa.Text)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=func.now()
    )


class Setting(Base):
    """單人設定（番茄時長、每日目標等）單列 JSONB，改設定免 migration。"""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
