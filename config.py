"""集中式設定：全部從環境變數讀，程式碼裡不寫死任何密鑰。

.env 由 python-dotenv 在 app 啟動時載入（.env 已列入 .gitignore，
絕對不要 commit）。
"""

import os
import pathlib


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- 基本 ---
SECRET_KEY = os.environ.get("ORGTD_SECRET_KEY", "")
DEBUG = _bool("ORGTD_DEBUG", False)

# --- org 檔存放 ---
# 每位使用者的受管資料夾：<ORG_ROOT>/<user.uuid>/
ORG_ROOT = pathlib.Path(
    os.environ.get("ORGTD_ORG_ROOT", "~/orgtd-data")
).expanduser().resolve()

# 是否允許使用者在設定頁自行指定絕對路徑。
#
# 自架單人使用時開啟（設成 ~/org，Emacs 直接開得到）。
# 對外的多人站台必須關閉：遠端使用者填的路徑是「伺服器」上的路徑，
# 開放等同讓任何註冊者對伺服器檔案系統任意寫入。
ALLOW_CUSTOM_ORG_DIR = _bool("ORGTD_ALLOW_CUSTOM_ORG_DIR", False)

# 是否開放註冊。作品集展示站可關閉，只留 demo 帳號。
ALLOW_REGISTRATION = _bool("ORGTD_ALLOW_REGISTRATION", True)

# --- OAuth ---
GOOGLE_CLIENT_ID = os.environ.get("ORGTD_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("ORGTD_GOOGLE_CLIENT_SECRET", "")
GOOGLE_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

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
        ORG_ROOT=ORG_ROOT,
        ALLOW_CUSTOM_ORG_DIR=ALLOW_CUSTOM_ORG_DIR,
        ALLOW_REGISTRATION=ALLOW_REGISTRATION,
        GOOGLE_ENABLED=GOOGLE_ENABLED,
    )
