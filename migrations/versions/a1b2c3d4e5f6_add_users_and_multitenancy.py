"""加入 users / oauth_accounts，並把所有既有表改為多租戶

原本是單人系統：nodes 等表沒有擁有者欄位，tags.name 與
weekly_reviews.week_start 是全域唯一。多人之後這兩個全域唯一鍵會直接
撞車（第二個使用者建同名標籤、或做同一週的回顧就會失敗），所以一併
改成 (user_id, X) 的複合唯一鍵。

既有資料一律歸給「站長」帳號（本次 migration 建立），不會遺失。

Revision ID: a1b2c3d4e5f6
Revises: 75f4b7372dbf
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "75f4b7372dbf"
branch_labels = None
depends_on = None

# 既有單人資料要掛在誰名下。email 可用環境變數覆寫，之後也能在設定頁改。
#
# 必須轉小寫：登入與 manage.py 都只用小寫查詢，這裡若存進含大寫的值，
# 站長會登不進去，而且因為 password_hash 是 NULL，連 manage.py set-password
# 也會因為同樣的大小寫問題找不到人，只剩直接改資料庫一途。
OWNER_EMAIL = os.environ.get("ORGTD_OWNER_EMAIL", "owner@localhost").strip().lower()
OWNER_NAME = os.environ.get("ORGTD_OWNER_NAME", "站長")

# 掛上 user_id 的既有資料表
_TENANT_TABLES = ("nodes", "tags", "pomodoro_sessions", "weekly_reviews", "settings")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("org_directory", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )

    # 建站長帳號。password_hash 留 NULL —— 尚未設定密碼，
    # 由 `flask user set-password` 或 Google 綁定後才能登入。
    conn = op.get_bind()
    owner_id = conn.execute(
        sa.text(
            """
            INSERT INTO users (uuid, email, display_name, org_directory, is_active)
            VALUES (gen_random_uuid()::text, :email, :name, NULL, true)
            RETURNING id
            """
        ),
        {"email": OWNER_EMAIL, "name": OWNER_NAME},
    ).scalar_one()

    # user_id 先開放 NULL → 回填 → 再上 NOT NULL，避免既有列擋住 DDL。
    for table in _TENANT_TABLES:
        op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
        conn.execute(
            sa.text(f"UPDATE {table} SET user_id = :owner"), {"owner": owner_id}
        )
        op.alter_column(table, "user_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_user", table, "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    # settings 從「固定第 1 列」改為一人一列
    op.create_unique_constraint("uq_settings_user", "settings", ["user_id"])

    # org 檔對應欄位。org_id 會寫進 org 檔的 :PROPERTIES: 抽屜，
    # 是 DB 列與 org 檔節點之間的錨點，既有節點先各配一個 UUID。
    op.add_column("nodes", sa.Column("org_id", sa.String(36), nullable=True))
    op.add_column("nodes", sa.Column("org_file", sa.String(255), nullable=True))
    conn.execute(sa.text("UPDATE nodes SET org_id = gen_random_uuid()::text"))
    op.alter_column("nodes", "org_id", nullable=False)
    op.create_unique_constraint("uq_nodes_user_org_id", "nodes", ["user_id", "org_id"])

    # 全域唯一鍵 → 每人唯一鍵
    op.drop_constraint("tags_name_key", "tags", type_="unique")
    op.create_unique_constraint("uq_tags_user_name", "tags", ["user_id", "name"])
    op.drop_constraint("weekly_reviews_week_start_key", "weekly_reviews", type_="unique")
    op.create_unique_constraint(
        "uq_reviews_user_week", "weekly_reviews", ["user_id", "week_start"]
    )

    # 索引重建：user_id 移到最左欄，否則多租戶篩選吃不到索引。
    op.drop_index("ix_nodes_parent", table_name="nodes")
    op.drop_index("ix_nodes_kind_state", table_name="nodes")
    op.drop_index("ix_nodes_scheduled", table_name="nodes")
    op.drop_index("ix_nodes_deadline", table_name="nodes")
    op.drop_index("ix_nodes_remind", table_name="nodes")
    op.drop_index("ix_pomo_node", table_name="pomodoro_sessions")
    op.drop_index("ix_pomo_started", table_name="pomodoro_sessions")

    op.create_index("ix_nodes_user_parent", "nodes", ["user_id", "parent_id"])
    op.create_index("ix_nodes_user_kind_state", "nodes", ["user_id", "kind", "todo_state"])
    op.create_index(
        "ix_nodes_user_scheduled",
        "nodes",
        ["user_id", "scheduled_at"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_nodes_user_deadline",
        "nodes",
        ["user_id", "deadline_at"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_nodes_user_remind",
        "nodes",
        ["user_id", "remind_at"],
        postgresql_where=sa.text("archived_at IS NULL AND notified_at IS NULL"),
    )
    op.create_index("ix_pomo_user_node", "pomodoro_sessions", ["user_id", "node_id"])
    op.create_index("ix_pomo_user_started", "pomodoro_sessions", ["user_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_pomo_user_started", table_name="pomodoro_sessions")
    op.drop_index("ix_pomo_user_node", table_name="pomodoro_sessions")
    op.drop_index("ix_nodes_user_remind", table_name="nodes")
    op.drop_index("ix_nodes_user_deadline", table_name="nodes")
    op.drop_index("ix_nodes_user_scheduled", table_name="nodes")
    op.drop_index("ix_nodes_user_kind_state", table_name="nodes")
    op.drop_index("ix_nodes_user_parent", table_name="nodes")

    op.create_index("ix_pomo_started", "pomodoro_sessions", ["started_at"])
    op.create_index("ix_pomo_node", "pomodoro_sessions", ["node_id"])
    op.create_index(
        "ix_nodes_remind",
        "nodes",
        ["remind_at"],
        postgresql_where=sa.text("archived_at IS NULL AND notified_at IS NULL"),
    )
    op.create_index(
        "ix_nodes_deadline",
        "nodes",
        ["deadline_at"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_nodes_scheduled",
        "nodes",
        ["scheduled_at"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index("ix_nodes_kind_state", "nodes", ["kind", "todo_state"])
    op.create_index("ix_nodes_parent", "nodes", ["parent_id"])

    # 還原全域唯一鍵前，多人資料會撞車，所以只保留單一使用者的列。
    op.drop_constraint("uq_reviews_user_week", "weekly_reviews", type_="unique")
    op.create_unique_constraint(
        "weekly_reviews_week_start_key", "weekly_reviews", ["week_start"]
    )
    op.drop_constraint("uq_tags_user_name", "tags", type_="unique")
    op.create_unique_constraint("tags_name_key", "tags", ["name"])

    op.drop_constraint("uq_nodes_user_org_id", "nodes", type_="unique")
    op.drop_column("nodes", "org_file")
    op.drop_column("nodes", "org_id")

    op.drop_constraint("uq_settings_user", "settings", type_="unique")
    for table in _TENANT_TABLES:
        op.drop_constraint(f"fk_{table}_user", table, type_="foreignkey")
        op.drop_column(table, "user_id")

    op.drop_table("oauth_accounts")
    op.drop_table("users")
