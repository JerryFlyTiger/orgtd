"""獨立提醒腳本：不靠瀏覽器，直接查資料庫、用 macOS 原生通知跳出提醒。

設計給 launchd 每分鐘呼叫一次（見 launchd/com.orgtd.notifier.plist、README）。
跟 agenda 頁面的輪詢（reminders.js）共用同一套「取出即標記已通知」邏輯
（queries.claim_due_reminders），兩邊誰先查到就算數，不會重複通知。
"""

import subprocess

from db import SessionLocal
from queries import claim_due_reminders


def _escape_applescript(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _notify(title):
    script = f'display notification "提醒事項到期" with title "{_escape_applescript(title)}"'
    subprocess.run(["osascript", "-e", script], check=False)


def main():
    with SessionLocal() as session:
        due = claim_due_reminders(session)
        for node in due:
            _notify(node.title)


if __name__ == "__main__":
    main()
