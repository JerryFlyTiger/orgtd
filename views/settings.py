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

import orgfiles
from db import SessionLocal
from models import User
from queries import get_settings, save_settings
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


@bp.post("/password")
@login_required
def change_password():
    old = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("new_password_confirm") or ""

    with SessionLocal() as session:
        user = session.get(User, uid())
        # 已有密碼者必須驗證舊密碼；還沒設過密碼的帳號第一次設定則不需要。
        if user.password_hash and not verify_password(user.password_hash, old):
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
