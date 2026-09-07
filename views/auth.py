"""註冊、登入、登出。

刻意只做本地帳號密碼一種登入方式。第三方登入（Google / Apple / X）
都需要在對應平台申請憑證、設定回呼網址，Apple 與 X 還要付費，
對這個專案的規模來說成本大於價值。
"""

import datetime

from flask import (
    Blueprint,
    current_app,
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
from emails import lookup_key, normalize_email
from security import hash_password, needs_rehash, verify_password

bp = Blueprint("auth", __name__)

# 密碼長度下限。依 NIST SP 800-63B，長度優先於「大小寫加符號」那類
# 複雜度規則——後者只會逼出 P@ssw0rd! 這種好猜又難記的密碼。
MIN_PASSWORD_LENGTH = 12


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
                    _finish_login(session, user)
                    flash("註冊完成。", "ok")
                    return redirect(url_for("settings.index"))

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
