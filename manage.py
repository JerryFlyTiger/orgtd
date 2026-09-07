"""管理指令：建帳號、設密碼、手動同步 org 檔。

用法：
    python manage.py set-password <email>
    python manage.py create-user <email> [顯示名稱]
    python manage.py list-users
    python manage.py export <email>     以資料庫為準重寫 org 檔
    python manage.py import <email>     以 org 檔為準回寫資料庫
"""

import getpass
import sys

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

import orgfiles  # noqa: E402
import orgsync  # noqa: E402
from db import SessionLocal  # noqa: E402
from models import User  # noqa: E402
from security import hash_password  # noqa: E402


def _get_user(session, email):
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        sys.exit(f"找不到帳號：{email}")
    return user


def _prompt_password():
    pw = getpass.getpass("新密碼（至少 12 字元，輸入時不顯示）：")
    if len(pw) < 12:
        sys.exit("密碼太短，至少 12 個字元。")
    if pw != getpass.getpass("再輸入一次："):
        sys.exit("兩次輸入不一致。")
    return pw


def set_password(email):
    pw = _prompt_password()
    with SessionLocal() as session:
        user = _get_user(session, email)
        user.password_hash = hash_password(pw)
        session.commit()
        print(f"已更新 {user.email} 的密碼。")


def create_user(email, display_name=None):
    pw = _prompt_password()
    with SessionLocal() as session:
        email = email.strip().lower()
        if session.scalar(select(User).where(User.email == email)):
            sys.exit(f"{email} 已經存在。")
        user = User(
            email=email,
            display_name=display_name or email.split("@")[0],
            password_hash=hash_password(pw),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        root = orgfiles.provision_user_directory(user)
        print(f"已建立 {user.email}，org 資料夾：{root}")


def list_users():
    with SessionLocal() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        if not users:
            print("目前沒有任何帳號。")
            return
        for u in users:
            pw = "有密碼" if u.password_hash else "無密碼（僅 OAuth）"
            print(f"  #{u.id}  {u.email:35s} {u.display_name:12s} {pw}")
            print(f"       org 資料夾：{orgfiles.user_org_dir(u)}")


def export(email):
    with SessionLocal() as session:
        user = _get_user(session, email)
        orgsync.rebuild_all(session, user)
        print(f"已依資料庫重寫 {orgfiles.user_org_dir(user)} 底下的 org 檔。")


def do_import(email):
    with SessionLocal() as session:
        user = _get_user(session, email)
        stats = orgsync.import_changed(session, user)
        if stats["files"]:
            print(
                f"已匯入 {'、'.join(stats['files'])}："
                f"新增 {stats['created']}、更新 {stats['updated']}、封存 {stats['archived']}。"
            )
        else:
            print("沒有偵測到外部改動。")


COMMANDS = {
    "set-password": (set_password, 1, 1),
    "create-user": (create_user, 1, 2),
    "list-users": (list_users, 0, 0),
    "export": (export, 1, 1),
    "import": (do_import, 1, 1),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    fn, lo, hi = COMMANDS[sys.argv[1]]
    args = sys.argv[2:]
    if not (lo <= len(args) <= hi):
        sys.exit(__doc__)
    fn(*args)


if __name__ == "__main__":
    main()
