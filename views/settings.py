"""設定頁：個人資料、番茄鐘參數、密碼，以及 org 檔匯出。"""

import io

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import orgfiles
from db import SessionLocal
from models import User
from queries import get_settings, save_settings
from emails import normalize_email
from security import hash_password, verify_password
from views._scope import uid

bp = Blueprint("settings", __name__, url_prefix="/settings")

_POMODORO_FIELDS = {
    "focus": (1, 180),
    "short": (1, 60),
    "long": (1, 120),
    "long_every": (1, 12),
}


@bp.route("/")
@login_required
def index():
    with SessionLocal() as session:
        user = session.get(User, uid())
        files = orgfiles.build_export(session, user.id)
        return render_template(
            "settings.html",
            user=user,
            pomodoro=get_settings(session, user.id),
            has_password=bool(user.password_hash),
            org_files=[
                {
                    "name": name,
                    "headlines": orgfiles.count_headlines(text),
                    "size": len(text.encode("utf-8")),
                }
                for name, text in files.items()
            ],
        )


@bp.post("/profile")
@login_required
def save_profile():
    name = (request.form.get("display_name") or "").strip()
    with SessionLocal() as session:
        user = session.get(User, uid())
        if name:
            user.display_name = name[:80]
            session.commit()
            flash("顯示名稱已更新。", "ok")
        else:
            flash("顯示名稱不可空白。", "warn")
    return redirect(url_for("settings.index"))


@bp.post("/pomodoro")
@login_required
def save_pomodoro():
    data = {}
    for field, (lo, hi) in _POMODORO_FIELDS.items():
        raw = request.form.get(field, type=int)
        if raw is None or not (lo <= raw <= hi):
            flash(f"{field} 必須是 {lo}–{hi} 之間的整數。", "warn")
            return redirect(url_for("settings.index"))
        data[field] = raw
    with SessionLocal() as session:
        save_settings(session, uid(), data)
    flash("番茄鐘設定已儲存。", "ok")
    return redirect(url_for("settings.index"))


def _password_ok(user, supplied: str) -> bool:
    """要動身分相關設定前的關卡。

    沒有密碼的帳號（舊單人資料遷移過來的狀態）不擋——它本來就還沒有
    可驗證的憑證，擋了會讓人連設定都進不去。
    """
    if not user.password_hash:
        return True
    return verify_password(user.password_hash, supplied)


def _email_taken(session, email: str, exclude_user_id: int) -> bool:
    """這個 email 是否已被別的帳號用走。

    唯一性先在應用層擋，是為了給得出人看得懂的訊息；資料庫的 unique 約束
    仍然是最後一道防線——兩個請求同時搶同一個 email 時，這個查詢會雙雙
    放行，真正擋下來的是 commit 時的約束。

    這則訊息會洩漏「該 email 已有帳號」。註冊頁本來就有同樣的洩漏，這裡
    再藏也擋不住枚舉，反而讓人不知道為什麼改不了。真要消除得改成「寄驗證
    信到新信箱」的流程，那需要寄信能力，這個專案沒有。
    """
    return session.scalar(
        select(User).where(User.email == email, User.id != exclude_user_id)
    ) is not None


@bp.post("/email")
@login_required
def change_email():
    """變更登入用的 email。

    要求輸入目前密碼：email 是帳號的主要識別，session 若被竊，改掉 email
    等於接管帳號，所以這裡不能只靠「已登入」就放行。
    """
    new_raw = request.form.get("new_email") or ""
    password = request.form.get("current_password") or ""

    with SessionLocal() as session:
        user = session.get(User, uid())

        if not _password_ok(user, password):
            flash("目前密碼不正確。", "warn")
            return redirect(url_for("settings.index"))

        new_email, error = normalize_email(new_raw)
        if error:
            flash(error, "warn")
            return redirect(url_for("settings.index"))

        if new_email == user.email:
            flash("新的 email 跟目前這個一樣。", "warn")
            return redirect(url_for("settings.index"))

        if _email_taken(session, new_email, user.id):
            flash("這個 email 已經被其他帳號使用了。", "warn")
            return redirect(url_for("settings.index"))

        user.email = new_email
        try:
            session.commit()
        except IntegrityError:
            # 上面的查詢到這裡的 commit 之間有空窗：另一個請求可能剛好把同一個
            # email 搶走。DB 的 unique 約束會擋下來，但如果不接住，使用者看到的
            # 是 500 而不是「這個 email 已經被使用了」。
            #
            # 前提：users 表目前只有 uuid 與 email 兩個 unique 約束，而這裡只
            # 改 email 欄位，所以到得了這裡的 IntegrityError 必然來自 email
            # 衝突。日後若在 users 上加其他約束，這段就得改成比對
            # e.orig.diag.constraint_name，否則會用「email 已被使用」蓋掉
            # 真正的原因。
            session.rollback()
            flash("這個 email 已經被其他帳號使用了。", "warn")
            return redirect(url_for("settings.index"))

    flash(f"email 已更新為 {new_email}。下次登入請用新的 email。", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/password")
@login_required
def change_password():
    old = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("new_password_confirm") or ""

    with SessionLocal() as session:
        user = session.get(User, uid())
        # 已有密碼者必須驗證舊密碼；還沒設過密碼的帳號第一次設定則不需要。
        if not _password_ok(user, old):
            flash("目前密碼不正確。", "warn")
            return redirect(url_for("settings.index"))
        if len(new) < 12:
            flash("新密碼至少 12 個字元。", "warn")
            return redirect(url_for("settings.index"))
        if new != confirm:
            flash("兩次輸入的新密碼不一致。", "warn")
            return redirect(url_for("settings.index"))
        user.password_hash = hash_password(new)
        session.commit()
    flash("密碼已更新。", "ok")
    return redirect(url_for("settings.index"))


# --------------------------------------------------------------------------
# org 檔匯出（單向，伺服器不留檔）
# --------------------------------------------------------------------------


@bp.get("/export.zip")
@login_required
def export_zip():
    with SessionLocal() as session:
        files = orgfiles.build_export(session, uid())
    return send_file(
        io.BytesIO(orgfiles.make_zip(files)),
        mimetype="application/zip",
        as_attachment=True,
        download_name="orgtd-org.zip",
    )


@bp.get("/export/<filename>")
@login_required
def export_one(filename):
    # 白名單比對而非路徑組合：使用者給的字串永遠不拿去拼路徑，
    # 從根本上沒有 ../ 穿越的餘地。
    if filename not in orgfiles.ORG_FILES:
        abort(404)
    with SessionLocal() as session:
        files = orgfiles.build_export(session, uid())
    return send_file(
        io.BytesIO(files[filename].encode("utf-8")),
        mimetype="text/plain; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )
