"""獨立提醒腳本：不靠瀏覽器，直接查資料庫、用 macOS 原生通知跳出提醒。

設計給 launchd 每分鐘呼叫一次（見 launchd/com.orgtd.notifier.plist、README）。
跟 agenda 頁面的輪詢（reminders.js）共用同一套「取出即標記已通知」邏輯
（queries.claim_due_reminders），兩邊誰先查到就算數，不會重複通知。

多人版注意：桌面通知只對「坐在這台機器前面的人」有意義，所以預設只處理
機器主人自己的提醒（ORGTD_NOTIFY_EMAIL 指定，否則取最早建立的帳號）。
把它改成掃全站，會把其他使用者的任務標題彈到主人的桌面上——那是資料外洩。
"""

import os
import subprocess

from sqlalchemy import select

from db import SessionLocal
from models import User
from queries import claim_due_reminders

NOTIFY_EMAIL = os.environ.get("ORGTD_NOTIFY_EMAIL", "").strip().lower()


def _escape_applescript(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _notify(title):
    script = f'display notification "提醒事項到期" with title "{_escape_applescript(title)}"'
    subprocess.run(["osascript", "-e", script], check=False)


def _target_user(session):
    if NOTIFY_EMAIL:
        return session.scalar(select(User).where(User.email == NOTIFY_EMAIL))
    # 沒指定就取最早建立的帳號 —— 自架情境下那就是機器主人。
    return session.scalars(select(User).order_by(User.id).limit(1)).first()


def main():
    with SessionLocal() as session:
        user = _target_user(session)
        if user is None:
            return
        due = claim_due_reminders(session, user.id)
        for node in due:
            _notify(node.title)


if __name__ == "__main__":
    main()
