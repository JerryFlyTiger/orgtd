"""跨 view 共用的進階查詢，把「有學問的查詢」集中封裝。

多租戶紀律：**每個函式都把 user_id 當必填位置參數**。刻意不給預設值、
也不從 flask 的 current_user 隱式取用——漏帶時會立刻 TypeError 當掉，
而不是安靜地把別人的資料查出來。跨租戶洩漏必須是編譯期等級的錯誤，
不能靠人記得加 where。
"""

import datetime

from sqlalchemy import func, select, text

from models import Node, PomodoroSession, Setting

_OPEN_STATES = ("TODO", "NEXT", "WAITING")

_DEFAULT_SETTINGS = {"focus": 25, "short": 5, "long": 15, "long_every": 4}

# path 陣列排序 = outline 展開順序；補 id 決勝，避免同層 position 重複時順序不穩定。
# 遞迴的每一層都再篩一次 user_id：即使有人偽造 root_id 指向別人的節點，
# 錨點那層就會查不到，子樹自然是空的。
_SUBTREE_SQL = text(
    """
    WITH RECURSIVE tree AS (
        SELECT id, parent_id, kind, title, todo_state, position, 1 AS depth,
               ARRAY[position] AS path
        FROM nodes
        WHERE id = :root_id AND user_id = :uid AND archived_at IS NULL
        UNION ALL
        SELECT n.id, n.parent_id, n.kind, n.title, n.todo_state, n.position,
               t.depth + 1, t.path || n.position
        FROM nodes n
        JOIN tree t ON n.parent_id = t.id
        WHERE n.archived_at IS NULL AND n.user_id = :uid
    )
    SELECT id, parent_id, kind, title, todo_state, position, depth
    FROM tree
    ORDER BY path, id
    """
)


def fetch_agenda(session, user_id):
    """回傳今天的 agenda：今日排程、逾期截止、以及所有 NEXT 行動。"""
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.timezone.utc)

    scheduled_today = session.scalars(
        select(Node)
        .where(
            Node.user_id == user_id,
            Node.archived_at.is_(None),
            Node.todo_state.in_(_OPEN_STATES),
            func.date(Node.scheduled_at) == today,
        )
        .order_by(Node.scheduled_at)
    ).all()

    overdue = session.scalars(
        select(Node)
        .where(
            Node.user_id == user_id,
            Node.archived_at.is_(None),
            Node.todo_state.in_(_OPEN_STATES),
            Node.deadline_at.is_not(None),
            Node.deadline_at < now,
        )
        .order_by(Node.deadline_at)
    ).all()

    next_actions = session.scalars(
        select(Node)
        .where(
            Node.user_id == user_id,
            Node.archived_at.is_(None),
            Node.todo_state == "NEXT",
        )
        .order_by(Node.created_at)
    ).all()

    return {
        "scheduled_today": scheduled_today,
        "overdue": overdue,
        "next_actions": next_actions,
    }


def fetch_subtree(session, user_id, root_id):
    """回傳以 root_id 為根的 outline 子樹，依 DFS 展開順序排列（含 root 自身，depth=1）。"""
    rows = session.execute(
        _SUBTREE_SQL, {"root_id": root_id, "uid": user_id}
    ).mappings().all()
    return [dict(row) for row in rows]


def has_next_action(session, user_id, project_id):
    """該專案底下（子孫）是否至少有一個 NEXT 行動 —— GTD 卡點偵測用。"""
    rows = fetch_subtree(session, user_id, project_id)
    return any(row["todo_state"] == "NEXT" for row in rows if row["id"] != project_id)


def is_descendant(session, user_id, node_id, maybe_ancestor_id):
    """node_id 是否為 maybe_ancestor_id 的子孫（含自己）—— 搬移節點時防止形成循環。"""
    rows = fetch_subtree(session, user_id, maybe_ancestor_id)
    return any(row["id"] == node_id for row in rows)


def list_projects(session, user_id):
    return session.scalars(
        select(Node)
        .where(
            Node.user_id == user_id,
            Node.kind == "project",
            Node.parent_id.is_(None),
            Node.archived_at.is_(None),
        )
        .order_by(Node.created_at)
    ).all()


def fetch_month_nodes(session, user_id, year, month):
    """該月所有「排程日或截止日落在這個月」的未封存節點。"""
    return session.scalars(
        select(Node).where(
            Node.user_id == user_id,
            Node.archived_at.is_(None),
            (
                (func.extract("year", Node.scheduled_at) == year)
                & (func.extract("month", Node.scheduled_at) == month)
            )
            | (
                (func.extract("year", Node.deadline_at) == year)
                & (func.extract("month", Node.deadline_at) == month)
            ),
        )
    ).all()


def claim_due_reminders(session, user_id=None):
    """取出所有到點且尚未通知的節點，並就地標記為已通知（at-most-once）。

    user_id 為 None 時掃全站——只有 notifier.py 這個背景程序會這樣用，
    它需要替所有使用者發提醒。Web 請求路徑一律要帶 user_id。
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    stmt = select(Node).where(
        Node.archived_at.is_(None),
        Node.remind_at.is_not(None),
        Node.remind_at <= now,
        Node.notified_at.is_(None),
    )
    if user_id is not None:
        stmt = stmt.where(Node.user_id == user_id)
    due = session.scalars(stmt).all()
    for node in due:
        node.notified_at = now
    if due:
        session.commit()
    return due


_SEARCH_NOTES_SQL = text(
    """
    SELECT id, title, body, similarity(title, :q) AS sim
    FROM nodes
    WHERE user_id = :uid AND kind = 'note' AND archived_at IS NULL
      AND (title ILIKE '%' || :q || '%' OR body ILIKE '%' || :q || '%')
    ORDER BY sim DESC, updated_at DESC
    LIMIT 50
    """
)


def search_notes(session, user_id, q):
    """中文用 pg_trgm 子字串模糊比對（PostgreSQL 內建全文搜尋不斷中文詞）。"""
    if not q:
        return []
    rows = session.execute(_SEARCH_NOTES_SQL, {"q": q, "uid": user_id}).mappings().all()
    return [dict(row) for row in rows]


def list_by_state(session, user_id, state):
    return session.scalars(
        select(Node)
        .where(
            Node.user_id == user_id,
            Node.todo_state == state,
            Node.archived_at.is_(None),
        )
        .order_by(Node.created_at)
    ).all()


def list_someday(session, user_id):
    return session.scalars(
        select(Node)
        .where(
            Node.user_id == user_id,
            Node.kind == "someday",
            Node.archived_at.is_(None),
        )
        .order_by(Node.created_at)
    ).all()


def fetch_upcoming(session, user_id, days=7):
    """未來 days 天內（含今天）有排程或截止的未封存項目。"""
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days - 1)
    return session.scalars(
        select(Node)
        .where(
            Node.user_id == user_id,
            Node.archived_at.is_(None),
            Node.todo_state.in_(_OPEN_STATES),
            (
                (func.date(Node.scheduled_at) >= today)
                & (func.date(Node.scheduled_at) <= end)
            )
            | (
                (func.date(Node.deadline_at) >= today)
                & (func.date(Node.deadline_at) <= end)
            ),
        )
        .order_by(func.coalesce(Node.scheduled_at, Node.deadline_at))
    ).all()


def projects_with_stalled_flag(session, user_id):
    """每個專案配一個 stalled 旗標（沒有 NEXT 行動）—— 週回顧的卡點偵測用。"""
    projects = list_projects(session, user_id)
    return [(p, not has_next_action(session, user_id, p.id)) for p in projects]


_WEEKLY_POMODORO_SQL = text(
    """
    SELECT COALESCE(p.title, task.title) AS project,
           count(*) AS pomodoros,
           SUM(ps.actual_seconds) AS total_seconds
    FROM pomodoro_sessions ps
    JOIN nodes task ON ps.node_id = task.id
    LEFT JOIN nodes p ON task.parent_id = p.id
    WHERE ps.user_id = :uid AND ps.completed AND ps.kind = 'focus'
      AND ps.started_at >= date_trunc('week', now())
    GROUP BY COALESCE(p.title, task.title)
    ORDER BY pomodoros DESC
    """
)


def weekly_pomodoro_stats(session, user_id):
    """本週（週一起算）每個專案／任務花了幾顆番茄、幾分鐘。"""
    rows = session.execute(_WEEKLY_POMODORO_SQL, {"uid": user_id}).mappings().all()
    return [dict(row) for row in rows]


_STREAK_SQL = text(
    """
    WITH daily AS (
        SELECT date_trunc('day', started_at)::date AS d
        FROM pomodoro_sessions
        WHERE user_id = :uid AND completed AND kind = 'focus'
        GROUP BY 1
    ),
    grp AS (
        SELECT d, d - (row_number() OVER (ORDER BY d))::int AS island
        FROM daily
    )
    SELECT count(*) AS streak_len, max(d) AS end_date
    FROM grp GROUP BY island ORDER BY end_date DESC LIMIT 1
    """
)


def current_streak(session, user_id):
    """目前連續完成番茄的天數；最近一次完成若超過一天前，streak 視為中斷（回傳 0）。"""
    row = session.execute(_STREAK_SQL, {"uid": user_id}).mappings().first()
    if row is None:
        return 0
    today = datetime.date.today()
    if row["end_date"] not in (today, today - datetime.timedelta(days=1)):
        return 0
    return row["streak_len"]


def count_inbox(session, user_id):
    return session.scalar(
        select(func.count())
        .select_from(Node)
        .where(
            Node.user_id == user_id,
            Node.kind == "inbox",
            Node.archived_at.is_(None),
        )
    )


def count_weekly_pomodoros(session, user_id):
    return session.scalar(
        select(func.count())
        .select_from(PomodoroSession)
        .where(
            PomodoroSession.user_id == user_id,
            PomodoroSession.completed.is_(True),
            PomodoroSession.kind == "focus",
            PomodoroSession.started_at >= func.date_trunc("week", func.now()),
        )
    )


def get_settings(session, user_id):
    """番茄鐘時長等個人設定，尚無設定列時回傳合理預設值（不寫入 DB）。"""
    setting = session.scalar(select(Setting).where(Setting.user_id == user_id))
    if setting is None:
        return dict(_DEFAULT_SETTINGS)
    return {**_DEFAULT_SETTINGS, **setting.data}


def save_settings(session, user_id, data: dict):
    """更新個人設定，沒有設定列就建一列。"""
    setting = session.scalar(select(Setting).where(Setting.user_id == user_id))
    if setting is None:
        setting = Setting(user_id=user_id, data={})
        session.add(setting)
    # 整份取代會讓未出現在表單裡的既有鍵消失，所以用合併。
    setting.data = {**(setting.data or {}), **data}
    session.commit()
    return setting.data


def count_today_pomodoros(session, user_id):
    """今天完成的專注番茄數（不含休息）。"""
    today = datetime.date.today()
    return session.scalar(
        select(func.count())
        .select_from(PomodoroSession)
        .where(
            PomodoroSession.user_id == user_id,
            PomodoroSession.completed.is_(True),
            PomodoroSession.kind == "focus",
            func.date(PomodoroSession.started_at) == today,
        )
    )


def count_due_today(session, user_id):
    """今日待辦數：今天到期排程 + 已逾期截止，供 nav 徽章顯示。"""
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.timezone.utc)
    return session.scalar(
        select(func.count())
        .select_from(Node)
        .where(
            Node.user_id == user_id,
            Node.archived_at.is_(None),
            Node.todo_state.in_(_OPEN_STATES),
            (func.date(Node.scheduled_at) == today)
            | (Node.deadline_at.is_not(None) & (Node.deadline_at < now)),
        )
    )
