"""GTD × org-mode 系統的白名單常數。

放常數而非 DB enum，是為了改動狀態機時不用跑 migration。
"""

# org 的 TODO 狀態機：NEXT 是 GTD 的「下一步行動」，是整套系統的心臟。
TODO_STATES = ["TODO", "NEXT", "WAITING", "DONE", "CANCELLED"]

# 節點語意分類：inbox 是尚未 clarify 的收集箱項目。
KINDS = ["inbox", "project", "task", "note", "someday"]

PRIORITIES = ["A", "B", "C"]

# 番茄鐘的三種計時型態。
POMODORO_KINDS = ["focus", "short_break", "long_break"]

# org 式重複規則：完成時不關閉，而是把排程/截止日往後推。
REPEAT_RULES = ["+1d", "+1w", "+1m"]
