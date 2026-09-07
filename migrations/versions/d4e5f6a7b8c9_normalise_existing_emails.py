"""把既有帳號的 email 正規化成與現行寫入規則一致

背景：早期的 users 寫入路徑沒有統一的正規化。
* a1b2c3d4e5f6 建立站長帳號時直接用 ORGTD_OWNER_EMAIL，沒轉小寫。
* 更早的註冊流程只做 .strip().lower()，沒有 Unicode NFC 正規化。

現在登入端與寫入端都走 emails.lookup_key() 的基準（IDNA/UTS-46 映射 +
Unicode 正規化 + 小寫，由 email_validator 決定，不是單純的 NFC），
但**改掉那些程式碼救不了已經存進資料庫的舊值**——Alembic 不會重跑已套用
的 revision，修正既有資料必須靠一支新的 migration。這就是那支。

正規化一律呼叫 emails.lookup_key()，跟登入端與 manage.py 同源。這支
migration 的語意就是「讓資料庫的值等於應用層查詢時會算出的鍵」，所以
跟著應用層走才是對的；自己複製一份邏輯必然漂移。

沒有正規化的帳號會出現這個症狀：密碼完全正確卻登不進去，而且因為系統
沒有忘記密碼流程，也沒有辦法自救。

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

import collections

import sqlalchemy as sa

from alembic import op
from emails import lookup_key

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, email FROM users")).mappings().all()

    # 直接用應用層的 lookup_key()，不自己重寫一套正規化。
    # 早先這裡是自製的 NFC + lower，跟 lookup_key 對不上：validate_email
    # 對網域還會做 IDNA/UTS-46 映射（全形 ｅｘａｍｐｌｅ → example），而且
    # 格式不合規則的帳號在 lookup_key 裡刻意不做 NFC。兩邊算出不同的鍵，
    # 這支「修正資料」的 migration 反而會把帳號改成登不進去。
    #
    # 每列只算一次：lookup_key 內部會做 IDNA 編碼，不是廉價操作。
    keys = {r["id"]: lookup_key(r["email"]) for r in rows}

    changes = [
        {"uid": r["id"], "email": keys[r["id"]]}
        for r in rows
        if keys[r["id"]] != r["email"]
    ]
    if not changes:
        return

    # 正規化後若有兩個帳號撞在一起（例如 A@x.com 與 a@x.com 各自存在），
    # unique 約束會擋下來。這裡刻意不自動合併或刪除任何一方——那是不可逆
    # 的資料決策，應該由人來做。直接讓 migration 失敗並附上說明。
    duplicates = {e for e, n in collections.Counter(keys.values()).items() if n > 1}
    if duplicates:
        raise RuntimeError(
            "以下 email 正規化之後會互相衝突，請先人工決定保留哪一個帳號："
            + "、".join(sorted(duplicates))
        )

    for change in changes:
        conn.execute(
            sa.text("UPDATE users SET email = :email WHERE id = :uid"), change
        )


def downgrade() -> None:
    # 舊值沒有保存，無法還原；正規化本身也不影響任何功能，故為 no-op。
    pass
