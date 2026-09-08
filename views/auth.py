"""註冊、登入、登出。

刻意只做本地帳號密碼一種登入方式。第三方登入（Google / Apple / X）
都需要在對應平台申請憑證、設定回呼網址，Apple 與 X 還要付費，
對這個專案的規模來說成本大於價值。
"""

import datetime

from flask import (
    Blueprint,
    current_app,
    make_response,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from db import SessionLocal
from models import User
import recovery
from emails import lookup_key, normalize_email
from security import hash_password, needs_rehash, verify_password

bp = Blueprint("auth", __name__)

# 密碼長度下限。依 NIST SP 800-63B，長度優先於「大小寫加符號」那類
# 複雜度規則——後者只會逼出 P@ssw0rd! 這種好猜又難記的密碼。
MIN_PASSWORD_LENGTH = 12

# 送給 consume() 的哨兵值：正整數主鍵永遠不會是負數，所以查詢必然落空，
# 但雜湊與資料庫往返照做，耗時跟真實帳號一致。
_NO_SUCH_USER = -1


def _safe_next(target: str | None) -> str:
    """只接受站內相對路徑，擋 open redirect（?next=//evil.com）。"""
    if not target:
        return url_for("inbox.index")
    if target.startswith("//") or "://" in target or not target.startswith("/"):
        return url_for("inbox.index")
    return target


def _create_user(session, *, email, display_name, password):
    """email 必須是已通過 normalize_email() 的值。"""
    user = User(
        email=email,
        display_name=display_name.strip()[:80] or email.split("@")[0],
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()  # 取得 user.id / user.uuid
    return user


def render_recovery_codes(codes, display_name, *, first_time):
    """救援碼明碼頁。註冊與設定頁重新產生都走這裡。

    刻意不加底線前綴：views/settings.py 會匯入它，是跨模組的公開介面。
    這個專案用底線標示「模組私有、外部別碰」（例如 views/_scope.py 是私有
    模組但匯出的 uid/owned_node 不加底線），改動簽章時要一併檢查呼叫端。

    這是全站唯一一個回應本文含長期有效機密的頁面（未使用的碼在被消耗前
    一直有效），所以明確禁止快取——否則在公用電腦上，按「上一頁」就能
    把明碼叫回來。
    """
    response = make_response(
        render_template(
            "auth/recovery_codes.html",
            codes=codes,
            display_name=display_name,
            first_time=first_time,
            download_text=recovery.format_for_download(display_name, codes),
        )
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    return response


def _finish_login(session, user) -> None:
    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    session.commit()
    login_user(user, remember=True)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("inbox.index"))
    if not current_app.config["ALLOW_REGISTRATION"]:
        flash("目前未開放註冊。", "warn")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        raw_email = request.form.get("email") or ""
        name = (request.form.get("display_name") or "").strip()
        pw = request.form.get("password") or ""
        pw2 = request.form.get("password_confirm") or ""

        errors = []
        email, email_error = normalize_email(raw_email)
        if email_error:
            errors.append(email_error)
        if len(pw) < MIN_PASSWORD_LENGTH:
            errors.append(f"密碼至少 {MIN_PASSWORD_LENGTH} 個字元。")
        if pw != pw2:
            errors.append("兩次輸入的密碼不一致。")

        if not errors:
            with SessionLocal() as session:
                if session.scalar(select(User).where(User.email == email)):
                    errors.append("這個 email 已經註冊過了。")
                else:
                    user = _create_user(
                        session, email=email, display_name=name or email, password=pw
                    )
                    codes = recovery.issue(session, user.id)
                    _finish_login(session, user)
                    # 直接渲染而非轉址：明碼絕不放進 session。Flask 的
                    # session cookie 只有簽章、沒有加密，內容是任何拿到
                    # cookie 的人都讀得出來的。
                    return render_recovery_codes(
                        codes, user.display_name, first_time=True
                    )

        for e in errors:
            flash(e, "warn")
        return render_template(
            "auth/register.html", email=raw_email.strip(), display_name=name
        )

    return render_template("auth/register.html", email="", display_name="")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inbox.index"))

    if request.method == "POST":
        raw_email = request.form.get("email") or ""
        pw = request.form.get("password") or ""
        # 查詢鍵一律走 lookup_key()，跟 manage.py 與資料修正 migration 同源。
        # 自己在這裡拼一套「去空白轉小寫」是行不通的：寫入端會做 Unicode
        # NFC 與 IDNA 映射，兩邊算出的鍵不同就永遠查不到人。
        email = lookup_key(raw_email)

        with SessionLocal() as session:
            user = session.scalar(select(User).where(User.email == email))
            # 帳號不存在與密碼錯誤回報同一則訊息，避免帳號枚舉。
            # verify_password 對 password_hash 為 NULL 的帳號一律回 False，
            # 尚未設定密碼的帳號因此無法被空密碼登入。
            if user and user.is_active and verify_password(user.password_hash, pw):
                if needs_rehash(user.password_hash):
                    user.password_hash = hash_password(pw)
                _finish_login(session, user)
                return redirect(_safe_next(request.args.get("next")))

        flash("email 或密碼不正確。", "warn")
        return render_template("auth/login.html", email=raw_email.strip())

    return render_template("auth/login.html", email="")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("已登出。", "ok")
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """用一次性救援碼重設密碼。

    刻意做成單一頁面一次送出（email + 救援碼 + 新密碼），而不是「先驗證
    再跳到重設頁」的兩段式：兩段式需要在中間存一個「已通過驗證」的狀態，
    那個狀態若設計不當就是繞過驗證的入口。一次送出沒有中間狀態可繞。
    """
    if current_user.is_authenticated:
        return redirect(url_for("settings.index"))

    if request.method == "GET":
        return render_template("auth/forgot_password.html", email="")

    raw_email = request.form.get("email") or ""
    code = request.form.get("recovery_code") or ""
    pw = request.form.get("new_password") or ""
    pw2 = request.form.get("new_password_confirm") or ""

    if len(pw) < MIN_PASSWORD_LENGTH:
        flash(f"新密碼至少 {MIN_PASSWORD_LENGTH} 個字元。", "warn")
        return render_template("auth/forgot_password.html", email=raw_email.strip())
    if pw != pw2:
        flash("兩次輸入的新密碼不一致。", "warn")
        return render_template("auth/forgot_password.html", email=raw_email.strip())

    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == lookup_key(raw_email)))

        # 帳號不存在、帳號停用、救援碼不對，一律回同一則訊息——訊息若有
        # 差異，就成了「這個 email 有沒有註冊過」的探測管道。
        #
        # 而且不論帳號存不存在都跑一次 consume()：寫成
        # `user is None or not recovery.consume(...)` 的話，Python 的短路會
        # 讓「查無此帳號」直接跳過雜湊與查詢，比「帳號存在但碼錯」明顯快，
        # 訊息藏好了卻從耗時洩漏出去。傳一個不可能存在的 user_id 讓兩條
        # 路徑做等量的工作。
        target_id = user.id if (user is not None and user.is_active) else _NO_SUCH_USER
        if not recovery.consume(session, target_id, code):
            flash("email 或救援碼不正確。", "warn")
            return render_template("auth/forgot_password.html", email=raw_email.strip())

        user.password_hash = hash_password(pw)
        session.commit()
        remaining = recovery.unused_count(session, user.id)

    flash("密碼已重設，請用新密碼登入。", "ok")
    if remaining == 0:
        flash("你的救援碼已全部用完，登入後請到設定頁重新產生。", "warn")
    elif remaining <= 2:
        flash(f"剩下 {remaining} 組救援碼，建議登入後重新產生一批。", "warn")
    return redirect(url_for("auth.login"))
