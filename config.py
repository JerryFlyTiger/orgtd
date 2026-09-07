"""集中式設定：全部從環境變數讀，程式碼裡不寫死任何密鑰。

.env 由 python-dotenv 在 app 啟動時載入（.env 已列入 .gitignore，
絕對不要 commit）。
"""

import os


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- 基本 ---
SECRET_KEY = os.environ.get("ORGTD_SECRET_KEY", "")
DEBUG = _bool("ORGTD_DEBUG", False)

# 是否開放註冊。作品集展示站可關閉，只留 demo 帳號。
ALLOW_REGISTRATION = _bool("ORGTD_ALLOW_REGISTRATION", True)

# --- Cookie 安全 ---
# SESSION_COOKIE_SECURE 在 HTTPS 上線後必須為 True，本機 http 開發要 False，
# 否則瀏覽器不會送 cookie、登入看起來像「登入成功但馬上被登出」。
SESSION_COOKIE_SECURE = _bool("ORGTD_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"


def apply(app) -> None:
    """把設定灌進 Flask app，並在缺少必要密鑰時明確擋下。"""
    secret = SECRET_KEY
    if not secret:
        if DEBUG:
            # 開發模式允許臨時密鑰；重啟會讓所有 session 失效，這是刻意的。
            secret = os.urandom(32).hex()
        else:
            raise RuntimeError(
                "未設定 ORGTD_SECRET_KEY。正式環境必須提供固定密鑰，"
                "否則每次重啟都會把所有人登出，且 session 可被偽造。\n"
                "產生方式：python -c \"import secrets; print(secrets.token_hex(32))\""
            )

    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        SESSION_COOKIE_HTTPONLY=SESSION_COOKIE_HTTPONLY,
        SESSION_COOKIE_SAMESITE=SESSION_COOKIE_SAMESITE,
        ALLOW_REGISTRATION=ALLOW_REGISTRATION,
    )
