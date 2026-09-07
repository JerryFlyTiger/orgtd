"""密碼雜湊與登入狀態。

email 的驗證與正規化在 emails.py。

雜湊用 argon2id（OWASP 現行首選，優於 bcrypt：抗 GPU 與抗記憶體權衡攻擊）。
argon2-cffi 的預設參數即為其建議值，不自訂以免調弱。
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask_login import LoginManager

_hasher = PasswordHasher()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "請先登入。"
login_manager.login_message_category = "warn"
# session 綁定 user agent + IP 雜湊，cookie 被竊時降低重放風險。
login_manager.session_protection = "strong"


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(password_hash: str | None, raw: str) -> bool:
    """驗證密碼。

    password_hash 為 None 代表這個帳號還沒設定密碼（例如從舊單人資料
    遷移過來的站長帳號）——一律拒絕，且不可回報「此帳號無密碼」之類的
    訊息，那會洩漏帳號是否存在。
    """
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """argon2 參數升級後，於下次成功登入時就地重算雜湊。"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False

