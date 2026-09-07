"""org 檔改為單向匯出，移除磁碟存放相關欄位

原本設計是把每位使用者的 org 檔長期保管在伺服器磁碟上，並雙向同步
（外部編輯能回寫資料庫）。實際評估後改成單向匯出：按下下載才即時算出
檔案，伺服器不留副本。

因此兩個欄位失去意義：
* users.org_directory —— 使用者自訂的資料夾路徑，已無資料夾可指
* nodes.org_file —— 節點目前落在哪個 org 檔，改為匯出時依 kind 即時決定

nodes.org_id 保留：它是匯出時寫進 :PROPERTIES: 的 :ID:，存在資料庫才能
保證同一個節點在多次匯出之間拿到同一個值，否則使用者在 Emacs 裡建立的
org-id 連結每匯出一次就失效。

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

import sqlalchemy as sa

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "org_directory")
    op.drop_column("nodes", "org_file")


def downgrade() -> None:
    op.add_column("users", sa.Column("org_directory", sa.Text(), nullable=True))
    op.add_column("nodes", sa.Column("org_file", sa.String(255), nullable=True))
