# 計畫：`orgtd` — GTD × org-mode 個人時間／專案管理系統

> 全新 Flask + PostgreSQL 本機單人網頁應用。放在 `/Users/jerrychen/My_Projects/orgtd`。

## Context（為什麼做這個）

Jerry 想要一套類似 org-mode 的個人時間管理／專案管理系統，涵蓋筆記、行事曆、提醒事項、番茄鐘、每週回顧，而且希望背後有**確實有效、被驗證過的方法論**，不只是功能堆疊。

關鍵洞察：他要的「每週回歸」正是 **GTD（Getting Things Done）** 的核心引擎（Weekly Review），而 org-mode 本就是實踐這類 workflow 的工具。因此本專案以 **GTD 五階段流程**為骨架、融合 **org-mode 操作精髓**、以**番茄鐘**作為執行與時間追蹤層，用 PostgreSQL 提供 JSON 檔做不到的能力（outline 樹查詢、模糊搜尋、時間統計）。

已確認的決策：
1. **方法論 = GTD 骨架**（收集箱 → 釐清 → 組織 → 每週回顧 → 執行）。
2. **純本機單人用**：不做帳號登入、不做多使用者，只在自己 Mac 上跑。
3. **前端 = 伺服器渲染 + 最小 JS**：純 Jinja2 + 手寫 CSS，只有番茄鐘計時器用少量原生 JavaScript。不引入 htmx / React / Vue / Bootstrap / Tailwind。
4. **架構以工程最佳實踐設計**（使用者明示不必遷就舊專案寫法）：blueprint + application factory、type hints、SQLAlchemy 2.0 現代寫法、清楚分層。保留與「寫法」無關的合理事項：繁中介面與註解、port 5001（避開 macOS AirPlay）、venv `.venv`。
5. **中文搜尋先做 trigram 子字串**（pg_trgm），README 留 zhparser 升級路徑。

## 方法論設計：GTD × org-mode × 番茄鐘

**GTD 五階段 ↔ 系統功能**

| GTD 階段 | 系統功能 | org 對應 |
|---|---|---|
| Capture 捕捉 | Inbox 收集箱（隨手快速輸入） | org-capture |
| Clarify 釐清 | 處理 Inbox：可執行？專案？參考？將來也許？ | 手動分類 |
| Organize 組織 | 專案／下一步行動（帶情境標籤）／等待中／將來也許／參考筆記 | outline + TODO 狀態 + tags |
| Reflect 回顧 | Weekly Review 引導式 wizard | agenda review |
| Engage 執行 | Agenda 今日視圖 + 番茄鐘 | agenda view + org-clock |

**org 精髓落地**
- **outline 階層**：節點用 `parent_id` 自關聯成大綱樹，用遞迴 CTE 查整棵樹。
- **TODO 狀態機**：`TODO → NEXT → WAITING → DONE / CANCELLED`（NEXT = GTD 的「下一步行動」，系統心臟）。
- **SCHEDULED / DEADLINE 分離**：「排程日」與「截止日」是兩個獨立欄位（org 關鍵設計，多數 todo app 混為一談），agenda 分別呈現。
- **標籤**：情境標籤（`@電腦`、`@電話`、`@外出`、`@閱讀`）+ 領域標籤，多對多。

**番茄鐘 = org-clock 具體化（差異化重點）**：番茄鐘綁定任務節點，每完成一顆就記錄到該任務，於是週回顧能回答「這週時間花在哪些專案」——把時間追蹤與 Pomodoro 合而為一。

## 架構決策（結論）

- **A. blueprint + application factory**：`app.py` 只留 `create_app()` + 啟動（`port=5001, debug=True`）。每功能一個 blueprint（`views/inbox.py` 等），內部照常 `@bp.route` + `render_template`。錯誤處理與 `context_processor` 註冊在 factory。理由：8 個頁面群 + DB session 生命週期 + Alembic 需要可 import 的 metadata，單檔會膨脹難維護。
- **B. 統一節點模型**（org 哲學：一切皆 outline 節點）：單一 `nodes` 表 + `parent_id` 自關聯為主幹，用 `kind` 區分語意（`inbox`/`project`/`task`/`note`/`someday`）、`todo_state` 表達狀態機。Inbox→Clarify→成為專案底下的 task，只是 UPDATE `kind`/`parent_id`/`todo_state`，**不用搬表**，完美對應 GTD 的流動性。只有真正異質的東西獨立成表：`pomodoro_sessions`、`weekly_reviews`、`tags`+`node_tags`、`settings`。**行事曆不需要獨立表** —— SCHEDULED/DEADLINE 是 `nodes` 上的兩個 timestamp。
- **C. DB 連線**：環境變數為主 + 合理預設。`db.py` 讀 `os.environ.get("ORGTD_DATABASE_URL", "postgresql+psycopg://jerry@localhost:5432/orgtd")`。Homebrew PG 本機用 macOS 使用者當 superuser、免密碼。
- **依賴**（`requirements.txt`）：`flask>=3.0`、`sqlalchemy>=2.0`、`psycopg[binary]>=3.1`、`alembic>=1.13`。建議用 Homebrew 的 Python 3.11+（現機是 Xcode 附帶的 3.9.6）。

## 資料模型（SQLAlchemy 2.0 `Mapped[]` 風格）

**`Node`（`nodes` 表）** — 核心：`id`、`parent_id`(FK self, ondelete CASCADE)、`kind`、`title`、`body`、`todo_state`、`priority`（A/B/C）、`scheduled_at`、`deadline_at`、`repeat_rule`（org 式重複規則：`+1d`/`+1w`/`+1m`；有此值的節點標成 DONE 時不關閉，而是把 scheduled_at/deadline_at 依規則往後推 —— 解決「每週回顧本身就是每週重複事件」等重複需求，不引入完整 RRULE 的複雜度）、`remind_at`（提醒時間，提醒機制見下節）、`position`（同層手動排序）、`archived_at`（軟封存）、`created_at`、`updated_at`、`done_at`、`search_vec`(TSVECTOR generated column，Alembic 中須用 `sa.Computed(...)` 定義)。關聯：`children`（自關聯 outline 樹）、`tags`（多對多）、`pomodoros`。
- 索引：`parent_id`；`(kind, todo_state)`；`scheduled_at` 與 `deadline_at` 各一條**部分索引** `WHERE archived_at IS NULL`；`search_vec` GIN；`title`/`body` 的 `gin_trgm_ops` GIN。
- `TODO_STATES` / `KINDS` / `PRIORITIES` 放 `constants.py` 白名單（不用 DB enum 綁死）。

**`Tag`**：`id`、`name`(unique)、`color`(#rrggbb)。
**`PomodoroSession`**：`id`、`node_id`(FK, SET NULL)、`started_at`、`ended_at`、`planned_minutes`、`actual_seconds`、`completed`(bool)、`kind`(focus/short_break/long_break)、`note`。索引 `node_id`、`started_at`。
**`WeeklyReview`**：`id`、`week_start`(Date, unique=該週週一)、`checklist`(JSONB)、`reflection`(Text)、`completed_at`。
**`Setting`**：單列 `data`(JSONB)，存番茄時長／每日目標等，改設定免 migration。

## PostgreSQL 特性用法（精華查詢封裝在 `queries.py`）

**遞迴 CTE — outline 子樹（專案樹頁）**
```sql
WITH RECURSIVE tree AS (
  SELECT id, parent_id, title, todo_state, kind, position, 1 AS depth, ARRAY[position] AS path
  FROM nodes WHERE id = :root_id
  UNION ALL
  SELECT n.id, n.parent_id, n.title, n.todo_state, n.kind, n.position,
         t.depth + 1, t.path || n.position
  FROM nodes n JOIN tree t ON n.parent_id = t.id
  WHERE n.archived_at IS NULL
)
SELECT * FROM tree ORDER BY path, id;   -- path 陣列排序 = outline 展開順序；補 id 決勝，避免同層 position 重複時順序不穩定
```
用 `session.execute(text(...))` 封裝成 `fetch_subtree(session, root_id)`。

**trigram 中文搜尋（Notes）** — PostgreSQL 內建 `to_tsvector` 不斷中文詞；用 `pg_trgm` 逐字元三元組做模糊子字串比對，中文可用：
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_nodes_title_trgm ON nodes USING gin (title gin_trgm_ops);
CREATE INDEX ix_nodes_body_trgm  ON nodes USING gin (body  gin_trgm_ops);
SELECT id, title, similarity(title, :q) AS sim
FROM nodes
WHERE (title ILIKE '%'||:q||'%' OR body ILIKE '%'||:q||'%') AND archived_at IS NULL
ORDER BY sim DESC LIMIT 50;
```
`search_vec` 用 `'simple'` 設定的 generated column，給英文/混合內容加權排序。README 須誠實說明「中文為子字串比對、未做語意斷詞，未來可裝 zhparser」。

**JSONB** — 週回顧 checklist（`{"clear_inbox": true, ...}`）、番茄設定。
**窗口函數 — streak**（連續完成天數，gaps-and-islands）：對每日有完成番茄的日期 `d - row_number() OVER (ORDER BY d)` 分島，取最新島長度。注意：「目前 streak」須額外檢查最新島的 `max(d)` 是今天或昨天，否則只是「歷史上最後一段連續」而非現在進行中的 streak。
**pg_trgm 擴充的建立時機**：放在第一支 Alembic migration 的最前面 `op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")`（Homebrew 本機使用者是 superuser，可直接建）。
**聚合 + date_trunc — 週番茄統計**：`pomodoro_sessions` JOIN `nodes` 任務再 LEFT JOIN 父專案，`WHERE started_at >= date_trunc('week', now())` GROUP BY 專案，得「時間花在哪些專案」。

## 提醒事項的落地（重審補上的缺口）

原計畫只靠瀏覽器 Notification API，但**網頁關著就收不到任何提醒** —— 這對「提醒事項」是致命缺口。修正為兩層機制：

1. **頁面開著時**：agenda 頁輕量輪詢（每 60 秒 fetch `/api/due`），到點的提醒用 Notification API 跳桌面通知（127.0.0.1 是 secure context，可用；Safari 需使用者手勢後才能要權限，首次進頁面放一顆「啟用通知」按鈕）。
2. **頁面關著時（macOS 原生通知）**：附一支獨立小腳本 `notifier.py`（查 PG 中 `remind_at <= now()` 且未通知的節點，用 `osascript -e 'display notification ...'` 發 macOS 通知，並回寫已通知標記），配一個 **launchd** plist 每分鐘跑一次。安裝方式寫進 README（`launchctl load ~/Library/LaunchAgents/com.orgtd.notifier.plist`）。這讓提醒不依賴瀏覽器，是真正可靠的本機提醒。

`nodes` 加 `notified_at` 欄位避免重複通知；repeat_rule 節點被推進下一輪時清空 `notified_at`。

## 頁面／路由

| 頁面 | Blueprint / 路由 | 關鍵查詢 |
|---|---|---|
| Inbox 收集箱 | `inbox` `GET /`、`POST /inbox/capture`、`POST /inbox/<id>/clarify` | `kind='inbox' AND archived_at IS NULL` |
| Agenda 今日 | `agenda` `GET /agenda` | 今天 SCHEDULED、逾期 DEADLINE、NEXT 行動（部分索引） |
| Projects 專案樹 | `projects` `GET /projects`、`GET /projects/<id>`、`POST /nodes/<id>/move` | 遞迴 CTE `fetch_subtree` |
| Calendar 行事曆 | `calendar` `GET /calendar/<int:year>/<int:month>` | 白名單驗證 year/month 否則 `abort(404)`；查該月區間 |
| Notes 筆記+搜尋 | `notes` `GET /notes`、`GET /notes/search?q=` | trigram 查詢 |
| Pomodoro 番茄鐘 | `pomodoro` `GET /pomodoro?node=<id>`、`POST /pomodoro/complete`、`POST /pomodoro/abandon` | INSERT session；今日番茄數 |
| Weekly Review | `review` `GET /review`、`GET /review/step/<n>`、`POST /review/save` | 各 step 對應查詢 |
| Dashboard 統計 | `dashboard` `GET /dashboard` | streak、週番茄、專案時間分布 |

**共用節點操作**：`POST /nodes/<id>/state`、`/schedule`、`/archive`，皆白名單驗證 + `abort(404)`。
**全域 badge**：用 `@app.context_processor` 注入 Inbox 未處理數／今日待辦數／本週番茄數，顯示在 nav。

## Weekly Review Wizard（差異化殺手級功能）

GTD 標準週回顧做成 8 步、資料驅動，狀態存 `weekly_reviews.checklist`(JSONB)，用 `GET /review/step/<n>` 前進（存 DB，不靠 JS 也能保存進度），頂端進度條 = `done_steps / 8`：

1. 清空收集箱（列 inbox 就地 clarify）2. 檢視 Next Actions 3. 檢視 Waiting For 4. **檢視 Projects：偵測「無 NEXT action 的專案」自動標紅**（GTD 卡點偵測，純 GTD 工具常漏）5. 檢視 Someday/Maybe 6. 檢視行事曆（未來 7 天）7. 回顧本週番茄統計 8. 自由反思 + 完成（寫 `completed_at`）。

## 番茄鐘整合

進 `/pomodoro?node=<id>` → 前端純倒數 → 走完一顆 `fetch POST /pomodoro/complete`（`node_id`、`actual_seconds`、`kind`）→ 後端 INSERT `completed=true` → 週回顧/Dashboard JOIN 聚合看時間分布。中途放棄 `POST /pomodoro/abandon`（`completed=false`）供誠實統計。

**`static/pomodoro.js`（唯一 JS，約 60–80 行 vanilla）**：`setInterval` 倒數更新 `document.title` 與畫面；狀態機 focus→short_break→（每 4 顆）long_break，時長讀模板注入的 `data-*`（來自 `settings`）；完成時 `Notification` API 桌面通知 + 提示音 + POST 記錄。頁面本身仍伺服器渲染。

## 專案結構

```
orgtd/
├── app.py            # create_app() 工廠 + main（port 5001, debug）
├── db.py             # engine / SessionLocal / Base / teardown session
├── models.py         # 所有 SQLAlchemy models（Mapped[] 風格）
├── queries.py        # 進階查詢封裝（遞迴 CTE、trigram 搜尋、統計）
├── constants.py      # TODO_STATES / KINDS / PRIORITIES 白名單
├── errors.py         # 自訂 Exception（NodeNotFound 等）
├── requirements.txt
├── README.md         # 安裝 PostgreSQL / 建 DB / 跑 migration / 啟動 + 新手 DB 白話說明
├── alembic.ini
├── migrations/       # env.py 指向 models.Base.metadata；versions/
├── views/            # inbox agenda projects calendar notes pomodoro review dashboard nodes
├── templates/        # base.html error.html 404.html + 各頁 + review/ + partials/（_node _tag _tree 遞迴 macro）
└── static/           # style.css（手寫，卡片式）pomodoro.js favicon.svg
```

## 里程碑（每個結束都能跑能用）

- **M0 骨架**：建立專案資料夾並**把本計畫書複製為 `orgtd/PLAN.md`**、venv、requirements、README 的 PostgreSQL 安裝段、`db.py`、`models.py`（先 `Node`）、Alembic 初始化 + 首次 migration、`base.html`/`style.css`/error 頁。驗證：起站、連 DB、`/` 顯示空 Inbox。
- **M1 最小閉環**：Inbox 捕捉 → clarify 成 task → 設 SCHEDULED → Agenda 今日檢視 → TODO 狀態機切換。**可日用的最小 GTD**。
- **M2 Projects + 標籤 + outline 樹**：`parent_id` 階層、遞迴 CTE、`_tree.html` 遞迴 macro、tags 多對多、部分索引。
- **M3 Calendar**：月曆頁、白名單 year/month + abort(404)、SCHEDULED/DEADLINE 落格。
- **M3.5 提醒**：`remind_at`/`notified_at`/`repeat_rule` 欄位、agenda 輪詢 + Notification、`notifier.py` + launchd（頁面關著也能提醒）。
- **M4 Pomodoro**：計時器 + `pomodoro.js` + 記錄 API + 今日番茄數（依賴 M1）。
- **M5 Notes + 搜尋**：note kind、pg_trgm 搜尋（含中文限制說明）。
- **M6 Weekly Review wizard**：8 步、JSONB checklist、無-NEXT 專案偵測（依賴 M1–M5）。
- **M7 Dashboard**：streak 窗口函數、週番茄聚合、專案時間分布、context_processor badge 全面接上（依賴 M4）。

## 端到端驗證

**建置／啟動（寫進 README）**
```sh
brew install postgresql@16 && brew services start postgresql@16   # 現機尚未安裝 PG
createdb orgtd
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head          # 建表 + 掛 pg_trgm 擴充
.venv/bin/python app.py                 # 開 http://127.0.0.1:5001
```

**逐功能驗證**
- DB 連線：`.venv/bin/python -c "from db import engine; print(engine.connect())"`。
- Inbox/Agenda：捕捉 → clarify 設今天 SCHEDULED → Agenda 出現 → 切 DONE 消失。
- Projects：建 project→子 task，樹正確縮排（驗證遞迴 CTE `path` 排序）。
- Calendar：`/calendar/2026/13` 應回 404（白名單驗證）。
- Pomodoro：跑一顆（改 settings 縮短測試）→ 桌面通知跳出 → `pomodoro_sessions` 多一列 `completed=true`。
- 搜尋：建含中文標題筆記，搜部分中文字串命中（驗證 trigram）。
- Weekly Review：清空 inbox 後 step1 打勾；建一個沒有 NEXT 的專案，step4 標紅。
- Dashboard：連兩天各完成番茄，streak 顯示 2。

**針對「沒碰過 DB」的 README 補強**：一段「SQLAlchemy / session / migration 是什麼」白話說明；`models.py` 關聯加繁中註解；`queries.py` 每條進階查詢附「這條 SQL 在做什麼」註解；附幾個可貼進 `psql` 練習的查詢。

## 可行性結論（2026-07-23 實機驗證）

實測本機：Python 3.9.6（系統版）、Homebrew 可用、PostgreSQL 未安裝、port 5001 空閒。結論：**可行**。

- Flask 3 / SQLAlchemy 2 / psycopg 3 / Alembic 在 Python 3.9 都能跑，但 3.9 已近生態下限（新版套件陸續要求 3.10+）。**決定：`brew install python@3.12` 用它建 venv**，一勞永逸。
- PostgreSQL 用 `brew install postgresql@17` + `brew services start postgresql@17`（README 記得提醒把 `/opt/homebrew/opt/postgresql@17/bin` 加進 PATH 才有 `psql`/`createdb`）。
- Notification API 在 `http://127.0.0.1:5001` 可用（localhost 屬 secure context）；頁面關閉時的提醒由 launchd + `notifier.py` 補上（見提醒章節）。

## 技術棧重評結論（不帶舊包袱）

greenfield 重比 Flask / Django / FastAPI：Django 的優勢是內建 admin 後台與 migration，但本專案的價值全在**自訂 UI**（agenda、專案樹、回顧 wizard、番茄鐘）—— 這些 Django admin 一項都幫不上，全都得自己寫，而 Django 換來的是更重的框架規約。FastAPI 主打 async API，與伺服器渲染為主的形態不合。**結論：維持 Flask + SQLAlchemy + Alembic**，它在「快速交付自訂 UI 的本機單人 PostgreSQL 應用」這個題目上仍是最短路徑。
