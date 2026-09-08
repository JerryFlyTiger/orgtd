"""加入一次性救援碼

忘記密碼的補救途徑。刻意不做「寄重設連結到信箱」：這個系統沒有寄信能力，
自架環境要接 SMTP 等於多一組要保管的憑證與一個會壞的外部相依。

code_hash 用 SHA-256 而非 argon2，理由見 models.RecoveryCode 的說明——
救援碼是程式產生的高熵隨機值，不是人選的低熵密碼。

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

import sqlalchemy as sa

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_recovery_user", "recovery_codes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_recovery_user", table_name="recovery_codes")
    op.drop_table("recovery_codes")
