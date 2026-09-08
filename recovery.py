"""救援碼：忘記密碼時重設用的一次性代碼。

產生 → 顯示一次 → 使用者自己保管。資料庫只存雜湊，所以即使整個資料庫
外洩，也還原不出可用的碼。

不相依 Flask，方便 manage.py 與測試直接使用。
"""

from __future__ import annotations

import datetime
import hashlib
import math
import secrets

from sqlalchemy import text as sa_text

# 去掉容易看錯的字元：0/O、1/I/L。使用者要用手抄或手打，可讀性比字母表
# 大小重要——少 7 個字元只讓每字元少約 0.5 bit。
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

GROUPS = 5          # 每組
GROUP_LENGTH = 5    # 5 個字元
CODE_COUNT = 10     # 一次發 10 組

# 輸入長度上限。正常的碼是 29 個字元（25 + 4 個連字號），放寬到 200 容忍
# 各種貼上時帶進來的空白與換行，但不讓人送進一個超大的字串。
_MAX_INPUT_LENGTH = 200

# 25 個字元 × log2(31) ≈ 124 位元。即使攻擊者知道帳號存在、且完全不受
# 速率限制，暴力搜尋在物理上也不可行——這是刻意不做嘗試次數限制的依據。
#
# 用 math.log2 而非 bit_length()：後者是無條件捨去（31 會算成 4 bit
# 而不是 4.95），會把強度低報成 100 位元。
CODE_ENTROPY_BITS = round(GROUPS * GROUP_LENGTH * math.log2(len(ALPHABET)))


def generate_code() -> str:
    """產生一組人類可抄寫的救援碼，例如 A7K2M-9PQRS-…"""
    groups = [
        "".join(secrets.choice(ALPHABET) for _ in range(GROUP_LENGTH))
        for _ in range(GROUPS)
    ]
    return "-".join(groups)


def canonical(raw: str) -> str:
    """把使用者輸入整理成比對用的形式。

    容忍小寫、空白、以及有沒有連字號——手抄回來的碼不該因為格式差異而
    被拒絕，那只會逼人放棄使用救援碼。
    """
    cleaned = "".join(c for c in (raw or "").upper() if c in ALPHABET)
    return "-".join(
        cleaned[i : i + GROUP_LENGTH] for i in range(0, len(cleaned), GROUP_LENGTH)
    )


def hash_code(code: str) -> str:
    """碼是高熵隨機值，用 SHA-256 即可（理由見 models.RecoveryCode）。"""
    return hashlib.sha256(canonical(code).encode("utf-8")).hexdigest()


def issue(session, user_id: int, count: int = CODE_COUNT) -> list[str]:
    """替使用者重新發一批救援碼，舊的全部作廢。

    回傳明文，**只有這一次拿得到**；資料庫只存雜湊。
    """
    from models import RecoveryCode

    session.query(RecoveryCode).filter(RecoveryCode.user_id == user_id).delete()

    codes = []
    for _ in range(count):
        code = generate_code()
        codes.append(code)
        session.add(RecoveryCode(user_id=user_id, code_hash=hash_code(code)))
    session.commit()
    return codes


def unused_count(session, user_id: int) -> int:
    from models import RecoveryCode

    return session.query(RecoveryCode).filter(
        RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None)
    ).count()


def consume(session, user_id: int, raw: str) -> bool:
    """驗證並用掉一組救援碼。成功回傳 True，且該碼立即失效。

    用單一敘述的 UPDATE ... WHERE used_at IS NULL ... RETURNING 做原子的
    比較並交換，而不是「先 SELECT 出來、再把 used_at 寫回去」。

    先查再寫在併發下守不住單次使用：兩個請求的 SELECT 都會在對方 commit
    之前讀到 used_at IS NULL，於是雙方都判定成功。這不是理論上的窄窗——
    實測 8 條併發打同一組碼，每一輪都有 6 到 8 條同時「消耗成功」。
    把條件放進 UPDATE 本身之後，資料庫的列鎖保證只有一條會更新到列，
    其餘拿回空的 RETURNING。
    """
    # 長度先擋掉再做正規化掃描：/forgot-password 是未登入端點，不該讓人
    # 用一個超大的欄位逼伺服器逐字元掃完才發現長度不對。
    if raw is None or len(raw) > _MAX_INPUT_LENGTH:
        return False

    normalised = canonical(raw)
    if len(normalised.replace("-", "")) != GROUPS * GROUP_LENGTH:
        return False

    row_id = session.execute(
        sa_text(
            """
            UPDATE recovery_codes
               SET used_at = now()
             WHERE user_id = :uid
               AND code_hash = :digest
               AND used_at IS NULL
            RETURNING id
            """
        ),
        {"uid": user_id, "digest": hash_code(normalised)},
    ).scalar()
    session.commit()
    return row_id is not None


def format_for_download(display_name: str, codes: list[str]) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "orgtd 救援碼",
        f"帳號：{display_name}",
        f"產生時間：{now}",
        "",
        "忘記密碼時，在登入頁點「忘記密碼」，用其中一組重設。",
        "每組只能用一次。用完或遺失請到設定頁重新產生。",
        "",
    ]
    lines += [f"  {i:2d}. {c}" for i, c in enumerate(codes, 1)]
    lines.append("")
    return "\n".join(lines)
