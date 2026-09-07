"""設定頁：個人資料、番茄鐘參數、org 資料夾位置與同步。"""

import pathlib
import tempfile

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select

import config
import orgfiles
import orgsync
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


def _org_status(user):
    """org 資料夾現況：路徑、每個檔案的大小與行數、是否有外部改動。"""
    root = orgfiles.user_org_dir(user)
    files = []
    for name in orgfiles.ORG_FILES:
        path = root / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            files.append(
                {
                    "name": name,
                    "size": path.stat().st_size,
                    "headlines": sum(1 for ln in text.split("\n") if ln.startswith("*")),
                    "mtime": path.stat().st_mtime,
                }
            )
        else:
            files.append({"name": name, "size": 0, "headlines": 0, "mtime": None})
    return {
        "root": str(root),
        "exists": root.exists(),
        "files": files,
        "total_bytes": orgfiles.dir_size_bytes(root),
        "changed": orgsync.externally_changed(user) if root.exists() else [],
        "editable": config.ALLOW_CUSTOM_ORG_DIR,
    }


@bp.route("/")
@login_required
def index():
    with SessionLocal() as session:
        user = session.get(User, uid())
        return render_template(
            "settings.html",
            user=user,
            pomodoro=get_settings(session, user.id),
            org=_org_status(user),
            has_password=bool(user.password_hash),
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
# org 資料夾
# --------------------------------------------------------------------------


@bp.post("/org/directory")
@login_required
def set_org_directory():
    if not config.ALLOW_CUSTOM_ORG_DIR:
        # 對外站台不接受使用者指定伺服器路徑——那等同任意檔案寫入。
        flash("這個站台不開放自訂資料夾路徑。", "warn")
        return redirect(url_for("settings.index"))

    path, error = orgfiles.validate_custom_dir(request.form.get("org_directory") or "")
    if error:
        flash(error, "warn")
        return redirect(url_for("settings.index"))

    with SessionLocal() as session:
        user = session.get(User, uid())
        user.org_directory = str(path)
        session.commit()
        orgfiles.provision_user_directory(user)
        orgsync.rebuild_all(session, user)
    flash(f"org 資料夾已設為 {path}，檔案已產生。", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/org/export")
@login_required
def export_to_files():
    """以 DB 為準，重新產生所有 org 檔。"""
    with SessionLocal() as session:
        user = session.get(User, uid())
        orgsync.rebuild_all(session, user)
    flash("已依資料庫內容重新產生 org 檔。", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/org/import")
@login_required
def import_from_files():
    """以 org 檔為準，把外部（Emacs / VSCode）的改動吃回資料庫。"""
    with SessionLocal() as session:
        user = session.get(User, uid())
        stats = orgsync.import_changed(session, user)
    if stats["files"]:
        flash(
            f"已從 {'、'.join(stats['files'])} 匯入："
            f"新增 {stats['created']}、更新 {stats['updated']}、封存 {stats['archived']}。",
            "ok",
        )
    else:
        flash("沒有偵測到外部改動。", "ok")
    return redirect(url_for("settings.index"))


@bp.get("/org/download")
@login_required
def download_zip():
    """打包下載，讓使用者拿到自己電腦上用 Emacs / VSCode 開。"""
    with SessionLocal() as session:
        user = session.get(User, uid())
        root = orgfiles.user_org_dir(user)
        if not root.exists():
            flash("org 資料夾尚未建立。", "warn")
            return redirect(url_for("settings.index"))
        tmp = pathlib.Path(tempfile.mkdtemp())
        archive = orgfiles.make_zip(root, tmp, "orgtd-org")
    return send_file(archive, as_attachment=True, download_name="orgtd-org.zip")
