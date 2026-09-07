"""移除 oauth_accounts

決定只保留 email + 密碼一種登入方式：第三方登入都要在對應平台申請憑證、
設定回呼網址，Apple 與 X 還要付費，對這個專案的規模成本大於價值。
留一張沒有任何程式碼會用到的表只是雜訊，所以一併移除。

日後要加回來的話，這份 migration 的 downgrade 就是建表腳本。

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

import sqlalchemy as sa

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("oauth_accounts")


def downgrade() -> None:
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
        # 供應商端的穩定使用者 ID（Google 的 sub）。刻意不用 email 當鍵——
        # email 可以在供應商端被改掉，sub 不會。
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )
