"""管理指令：建帳號、設密碼、發救援碼、匯出 org 檔。

用法：
    python manage.py set-password <email>
    python manage.py create-user <email> [顯示名稱]
    python manage.py list-users
    python manage.py recovery-codes <email>
    python manage.py export <email> <目標資料夾>
"""

import getpass
import sys

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

import pathlib  # noqa: E402

import orgfiles  # noqa: E402
import recovery  # noqa: E402
from db import SessionLocal  # noqa: E402
from models import User  # noqa: E402
from emails import lookup_key, normalize_email  # noqa: E402
from security import hash_password  # noqa: E402


def _get_user(session, email):
    # 查詢鍵跟 login() 與資料修正 migration 同源，見 emails.lookup_key。
    user = session.scalar(select(User).where(User.email == lookup_key(email)))
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
    # email 先驗完再問密碼：格式打錯的話，不該讓人白打兩次密碼才被退回。
    normalised, error = normalize_email(email)
    if error:
        sys.exit(error)
    pw = _prompt_password()
    with SessionLocal() as session:
        email = normalised
        if session.scalar(select(User).where(User.email == email)):
            sys.exit(f"{email} 已經存在。")
        user = User(
            email=email,
            display_name=display_name or email.split("@")[0],
            password_hash=hash_password(pw),
        )
        session.add(user)
        session.commit()
        print(f"已建立 {email}。")


def list_users():
    with SessionLocal() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        if not users:
            print("目前沒有任何帳號。")
            return
        for u in users:
            pw = "有密碼" if u.password_hash else "尚未設定密碼，無法登入"
            print(f"  #{u.id}  {u.email:35s} {u.display_name:12s} {pw}")


def recovery_codes(email):
    """重新發一批救援碼。舊的全部作廢。

    這是連救援碼都遺失時的最後手段——能跑這個指令代表你有這台機器的
    存取權，本來就等同擁有這個系統的一切。
    """
    with SessionLocal() as session:
        user = _get_user(session, email)
        codes = recovery.issue(session, user.id)
        print(recovery.format_for_download(user.display_name, codes))
        print("以上只會顯示這一次，請立刻存起來。")


def export(email, target_dir):
    """把 org 檔寫到指定資料夾，供 Emacs / VSCode 開啟。"""
    root = pathlib.Path(target_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as session:
        user = _get_user(session, email)
        files = orgfiles.build_export(session, user.id)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
        print(f"  {name}  ({orgfiles.count_headlines(text)} 個節點)")
    print(f"已匯出到 {root}")


COMMANDS = {
    "set-password": (set_password, 1, 1),
    "create-user": (create_user, 1, 2),
    "list-users": (list_users, 0, 0),
    "recovery-codes": (recovery_codes, 1, 1),
    "export": (export, 2, 2),
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
