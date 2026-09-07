"""註冊、登入、登出，以及 Google OAuth 2.0 (OIDC) 綁定。"""

import datetime

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

import config
from db import SessionLocal
from models import OAuthAccount, User
from orgfiles import provision_user_directory
from security import hash_password, needs_rehash, verify_password

bp = Blueprint("auth", __name__)

oauth = OAuth()


def init_oauth(app) -> None:
    oauth.init_app(app)
    if config.GOOGLE_ENABLED:
        # 用 OIDC discovery 文件而非寫死端點：Google 換 endpoint 時不必改程式。
        oauth.register(
            name="google",
            client_id=config.GOOGLE_CLIENT_ID,
            client_secret=config.GOOGLE_CLIENT_SECRET,
            server_metadata_url=(
                "https://accounts.google.com/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid email profile"},
        )


def _safe_next(target: str | None) -> str:
    """只接受站內相對路徑，擋 open redirect（?next=//evil.com）。"""
    if not target:
        return url_for("inbox.index")
    if target.startswith("//") or "://" in target:
        return url_for("inbox.index")
    if not target.startswith("/"):
        return url_for("inbox.index")
    return target


def _create_user(session, *, email, display_name, password=None):
    user = User(
        email=email.strip().lower(),
        display_name=display_name.strip()[:80] or email.split("@")[0],
        password_hash=hash_password(password) if password else None,
    )
    session.add(user)
    session.flush()  # 取得 user.id / user.uuid
    provision_user_directory(user)
    return user


def _finish_login(session, user) -> None:
    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    session.commit()
    login_user(user, remember=True)


# --------------------------------------------------------------------------
# 本地帳號密碼
# --------------------------------------------------------------------------


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("inbox.index"))
    if not current_app.config["ALLOW_REGISTRATION"]:
        flash("目前未開放註冊。", "warn")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("display_name") or "").strip()
        pw = request.form.get("password") or ""
        pw2 = request.form.get("password_confirm") or ""

        errors = []
        if "@" not in email or len(email) > 255:
            errors.append("請填寫有效的 email。")
        if len(pw) < 12:
            # NIST SP 800-63B：長度優先於複雜度規則，不強制大小寫符號組合。
            errors.append("密碼至少 12 個字元。")
        if pw != pw2:
            errors.append("兩次輸入的密碼不一致。")

        if not errors:
            with SessionLocal() as session:
                exists = session.scalar(select(User).where(User.email == email))
                if exists:
                    errors.append("這個 email 已經註冊過了。")
                else:
                    user = _create_user(
                        session, email=email, display_name=name or email, password=pw
                    )
                    _finish_login(session, user)
                    flash("註冊完成，已為你建立 org 資料夾。", "ok")
                    return redirect(url_for("settings.index"))

        for e in errors:
            flash(e, "warn")
        return render_template("auth/register.html", email=email, display_name=name)

    return render_template("auth/register.html", email="", display_name="")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inbox.index"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""

        with SessionLocal() as session:
            user = session.scalar(select(User).where(User.email == email))
            # 帳號不存在與密碼錯誤回報同一則訊息，避免帳號枚舉。
            if user and user.is_active and verify_password(user.password_hash, pw):
                if needs_rehash(user.password_hash):
                    user.password_hash = hash_password(pw)
                _finish_login(session, user)
                return redirect(_safe_next(request.args.get("next")))

        flash("email 或密碼不正確。", "warn")
        return render_template("auth/login.html", email=email)

    return render_template("auth/login.html", email="")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("已登出。", "ok")
    return redirect(url_for("auth.login"))


# --------------------------------------------------------------------------
# Google OAuth 2.0 / OpenID Connect
# --------------------------------------------------------------------------


@bp.get("/auth/google")
def google_start():
    if not config.GOOGLE_ENABLED:
        flash("尚未設定 Google 登入。", "warn")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.get("/auth/google/callback")
def google_callback():
    if not config.GOOGLE_ENABLED:
        return redirect(url_for("auth.login"))

    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash("Google 登入失敗，請再試一次。", "warn")
        return redirect(url_for("auth.login"))

    claims = token.get("userinfo") or {}
    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    if not sub:
        flash("Google 未回傳帳號識別碼。", "warn")
        return redirect(url_for("auth.login"))

    with SessionLocal() as session:
        link = session.scalar(
            select(OAuthAccount).where(
                OAuthAccount.provider == "google",
                OAuthAccount.provider_user_id == sub,
            )
        )
        if link:
            _finish_login(session, link.user)
            return redirect(url_for("inbox.index"))

        # 尚未綁定。若 email 已有本地帳號就併過去，但**必須** Google 已驗證
        # 該 email——否則任何人只要在自己的供應商端填上別人的 email，
        # 就能接管既有帳號。
        user = None
        if email and claims.get("email_verified"):
            user = session.scalar(select(User).where(User.email == email))

        if user is None:
            if not current_app.config["ALLOW_REGISTRATION"]:
                flash("目前未開放註冊。", "warn")
                return redirect(url_for("auth.login"))
            if not email:
                flash("Google 未提供 email，無法建立帳號。", "warn")
                return redirect(url_for("auth.login"))
            user = _create_user(
                session, email=email, display_name=claims.get("name") or email
            )

        session.add(
            OAuthAccount(user_id=user.id, provider="google", provider_user_id=sub)
        )
        _finish_login(session, user)
        flash("已用 Google 帳號登入。", "ok")
        return redirect(url_for("inbox.index"))
