"""跨 view 共用的進階查詢，把「有學問的查詢」集中封裝。"""

import datetime

from sqlalchemy import func, select, text

from models import Node, PomodoroSession, Setting

_OPEN_STATES = ("TODO", "NEXT", "WAITING")

_DEFAULT_SETTINGS = {"focus": 25, "short": 5, "long": 15, "long_every": 4}

# path 陣列排序 = outline 展開順序；補 id 決勝，避免同層 position 重複時順序不穩定。
_SUBTREE_SQL = text(
    """
    WITH RECURSIVE tree AS (
        SELECT id, parent_id, kind, title, todo_state, position, 1 AS depth,
               ARRAY[position] AS path
        FROM nodes
        WHERE id = :root_id AND archived_at IS NULL
        UNION ALL
        SELECT n.id, n.parent_id, n.kind, n.title, n.todo_state, n.position,
               t.depth + 1, t.path || n.position
        FROM nodes n
        JOIN tree t ON n.parent_id = t.id
        WHERE n.archived_at IS NULL
    )
    SELECT id, parent_id, kind, title, todo_state, position, depth
    FROM tree
    ORDER BY path, id
    """
)


def fetch_agenda(session):
    """回傳今天的 agenda：今日排程、逾期截止、以及所有 NEXT 行動。"""
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.timezone.utc)

    scheduled_today = session.scalars(
        select(Node)
        .where(
            Node.archived_at.is_(None),
            Node.todo_state.in_(_OPEN_STATES),
            func.date(Node.scheduled_at) == today,
        )
        .order_by(Node.scheduled_at)
    ).all()

    overdue = session.scalars(
        select(Node)
        .where(
            Node.archived_at.is_(None),
            Node.todo_state.in_(_OPEN_STATES),
            Node.deadline_at.is_not(None),
            Node.deadline_at < now,
        )
        .order_by(Node.deadline_at)
    ).all()

    next_actions = session.scalars(
        select(Node)
        .where(Node.archived_at.is_(None), Node.todo_state == "NEXT")
        .order_by(Node.created_at)
    ).all()

    return {
        "scheduled_today": scheduled_today,
        "overdue": overdue,
        "next_actions": next_actions,
    }


def fetch_subtree(session, root_id):
    """回傳以 root_id 為根的 outline 子樹，依 DFS 展開順序排列（含 root 自身，depth=1）。"""
    rows = session.execute(_SUBTREE_SQL, {"root_id": root_id}).mappings().all()
    return [dict(row) for row in rows]


def has_next_action(session, project_id):
    """該專案底下（子孫）是否至少有一個 NEXT 行動 —— GTD 卡點偵測用。"""
    rows = fetch_subtree(session, project_id)
    return any(row["todo_state"] == "NEXT" for row in rows if row["id"] != project_id)


def is_descendant(session, node_id, maybe_ancestor_id):
    """node_id 是否為 maybe_ancestor_id 的子孫（含自己）—— 搬移節點時防止形成循環。"""
    rows = fetch_subtree(session, maybe_ancestor_id)
    return any(row["id"] == node_id for row in rows)


def list_projects(session):
    return session.scalars(
        select(Node)
        .where(Node.kind == "project", Node.parent_id.is_(None), Node.archived_at.is_(None))
        .order_by(Node.created_at)
    ).all()


def fetch_month_nodes(session, year, month):
    """該月所有「排程日或截止日落在這個月」的未封存節點。"""
    return session.scalars(
        select(Node).where(
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


def claim_due_reminders(session):
    """取出所有到點且尚未通知的節點，並就地標記為已通知（at-most-once）。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    due = session.scalars(
        select(Node).where(
            Node.archived_at.is_(None),
            Node.remind_at.is_not(None),
            Node.remind_at <= now,
            Node.notified_at.is_(None),
        )
    ).all()
    for node in due:
        node.notified_at = now
    if due:
        session.commit()
    return due


_SEARCH_NOTES_SQL = text(
    """
    SELECT id, title, body, similarity(title, :q) AS sim
    FROM nodes
    WHERE kind = 'note' AND archived_at IS NULL
      AND (title ILIKE '%' || :q || '%' OR body ILIKE '%' || :q || '%')
    ORDER BY sim DESC, updated_at DESC
    LIMIT 50
    """
)


def search_notes(session, q):
    """中文用 pg_trgm 子字串模糊比對（PostgreSQL 內建全文搜尋不斷中文詞）。"""
    if not q:
        return []
    rows = session.execute(_SEARCH_NOTES_SQL, {"q": q}).mappings().all()
    return [dict(row) for row in rows]


def list_by_state(session, state):
    return session.scalars(
        select(Node)
        .where(Node.todo_state == state, Node.archived_at.is_(None))
        .order_by(Node.created_at)
    ).all()


def list_someday(session):
    return session.scalars(
        select(Node)
        .where(Node.kind == "someday", Node.archived_at.is_(None))
        .order_by(Node.created_at)
    ).all()


def fetch_upcoming(session, days=7):
    """未來 days 天內（含今天）有排程或截止的未封存項目。"""
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days - 1)
    return session.scalars(
        select(Node)
        .where(
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


def projects_with_stalled_flag(session):
    """每個專案配一個 stalled 旗標（沒有 NEXT 行動）—— 週回顧的卡點偵測用。"""
    projects = list_projects(session)
    return [(p, not has_next_action(session, p.id)) for p in projects]


_WEEKLY_POMODORO_SQL = text(
    """
    SELECT COALESCE(p.title, task.title) AS project,
           count(*) AS pomodoros,
           SUM(ps.actual_seconds) AS total_seconds
    FROM pomodoro_sessions ps
    JOIN nodes task ON ps.node_id = task.id
    LEFT JOIN nodes p ON task.parent_id = p.id
    WHERE ps.completed AND ps.kind = 'focus'
      AND ps.started_at >= date_trunc('week', now())
    GROUP BY COALESCE(p.title, task.title)
    ORDER BY pomodoros DESC
    """
)


def weekly_pomodoro_stats(session):
    """本週（週一起算）每個專案／任務花了幾顆番茄、幾分鐘。"""
    rows = session.execute(_WEEKLY_POMODORO_SQL).mappings().all()
    return [dict(row) for row in rows]


_STREAK_SQL = text(
    """
    WITH daily AS (
        SELECT date_trunc('day', started_at)::date AS d
        FROM pomodoro_sessions
        WHERE completed AND kind = 'focus'
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


def current_streak(session):
    """目前連續完成番茄的天數；最近一次完成若超過一天前，streak 視為中斷（回傳 0）。"""
    row = session.execute(_STREAK_SQL).mappings().first()
    if row is None:
        return 0
    today = datetime.date.today()
    if row["end_date"] not in (today, today - datetime.timedelta(days=1)):
        return 0
    return row["streak_len"]


def count_inbox(session):
    return session.scalar(
        select(func.count())
        .select_from(Node)
        .where(Node.kind == "inbox", Node.archived_at.is_(None))
    )


def count_weekly_pomodoros(session):
    return session.scalar(
        select(func.count())
        .select_from(PomodoroSession)
        .where(
            PomodoroSession.completed.is_(True),
            PomodoroSession.kind == "focus",
            PomodoroSession.started_at >= func.date_trunc("week", func.now()),
        )
    )


def get_settings(session):
    """番茄鐘時長等單人設定，尚無設定列時回傳合理預設值（不寫入 DB）。"""
    setting = session.get(Setting, 1)
    if setting is None:
        return dict(_DEFAULT_SETTINGS)
    return {**_DEFAULT_SETTINGS, **setting.data}


def count_today_pomodoros(session):
    """今天完成的專注番茄數（不含休息）。"""
    today = datetime.date.today()
    return session.scalar(
        select(func.count())
        .select_from(PomodoroSession)
        .where(
            PomodoroSession.completed.is_(True),
            PomodoroSession.kind == "focus",
            func.date(PomodoroSession.started_at) == today,
        )
    )


def count_due_today(session):
    """今日待辦數：今天到期排程 + 已逾期截止，供 nav 徽章顯示。"""
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.timezone.utc)
    return session.scalar(
        select(func.count())
        .select_from(Node)
        .where(
            Node.archived_at.is_(None),
            Node.todo_state.in_(_OPEN_STATES),
            (func.date(Node.scheduled_at) == today)
            | (Node.deadline_at.is_not(None) & (Node.deadline_at < now)),
        )
    )
