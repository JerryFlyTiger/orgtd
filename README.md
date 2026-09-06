# orgtd

GTD × org-mode 風格的個人時間／專案管理系統。純本機單人使用，Flask + PostgreSQL。設計理念與完整架構見 [`PLAN.md`](PLAN.md)。

## 安裝（首次）

```sh
# 1. 安裝 PostgreSQL 17 與 Python 3.12（本專案用的版本，比系統內建的 Python 3.9 新）
brew install postgresql@17 python@3.12
brew services start postgresql@17

# 2. 把 PostgreSQL 的指令加進 PATH（psql / createdb），可加進 ~/.zshrc 永久生效
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"

# 3. 建立資料庫（Homebrew 版直接用你的 macOS 帳號當 superuser，免密碼）
createdb orgtd

# 4. 建立虛擬環境並安裝依賴
cd ~/My_Projects/orgtd
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 5. 建表（跑資料庫遷移）
.venv/bin/alembic upgrade head
```

## 啟動

```sh
.venv/bin/python app.py
```

開瀏覽器 <http://127.0.0.1:5001>。

> `debug=True` 僅供本機開發使用，請勿對外開放。

### 用雙擊圖示啟動（不用開 Terminal）

`gui/` 資料夾裡有三個編譯好的 `.app`，可以直接拖到 Desktop 或 Dock：

- **「啟動 orgtd.app」**：雙擊啟動伺服器（背景執行，不會跳出 Terminal 視窗），成功/失敗都會用系統通知或對話框告知。已經在跑時會友善提示，不會重複啟動；如果 5001 port 被別的程式占用會提示衝突，不會誤殺陌生程式。
- **「關閉 orgtd.app」**：雙擊乾淨關閉伺服器。本來就沒在跑時會友善提示而非報錯。
- **「開啟 orgtd 網頁.app」**：雙擊用預設瀏覽器開啟 <http://127.0.0.1:5001>。

這三個 `.app` 是用 `gui/build.sh` 從對應的 `.applescript` 原始碼編譯出來的（`osacompile`，macOS 內建工具，不需要額外安裝任何東西）。改了 `.applescript` 之後重跑 `gui/build.sh` 就會重新編譯。

技術細節：雙擊啟動時會自動設定 `ORGTD_NO_RELOAD=1` 環境變數關掉 Flask 的 reloader，讓伺服器只有單一 process（方便乾淨追蹤與關閉）；手動用 Terminal 跑 `.venv/bin/python app.py`（不設這個環境變數）行為完全不變，reloader 照常開著方便開發除錯。行程 PID 與啟動 log 分別存在 `run/orgtd.pid`、`run/server.log`（不進版控）。

## 資料庫小白話（給沒碰過 ORM 的人）

- **SQLAlchemy** 是 Python 的 ORM（物件關聯對映）：讓你用 Python class（`models.py` 裡的 `Node`、`Tag` 等）操作資料庫的表，不用手寫大部分 SQL。
- **Session** 是一次「跟資料庫對話」的單位：開一個 session、查詢/新增/修改資料、`commit()` 確認寫入、關閉。本專案在每個 view 函式裡用 `with SessionLocal() as session:` 開關，一個請求一個 session。
- **Migration（遷移）** 是資料庫表結構的版本控制：`models.py` 改了欄位後，用 `alembic revision --autogenerate -m "說明"` 產生一份「怎麼把舊表改成新表」的腳本，再用 `alembic upgrade head` 實際套用到資料庫。這樣資料庫結構的每次變動都有紀錄、可回溯。

## 常用指令

```sh
# 改了 models.py 後，產生新的 migration
.venv/bin/alembic revision --autogenerate -m "說明這次改了什麼"
.venv/bin/alembic upgrade head

# 直接查資料庫（練習 SQL 很方便）
psql -d orgtd
```

在 `psql` 裡可以練習的查詢範例：

```sql
-- 看目前所有節點
SELECT id, kind, title, todo_state FROM nodes ORDER BY id;

-- 看某個節點底下的子樹（outline 大綱）
WITH RECURSIVE tree AS (
  SELECT id, parent_id, title, position, 1 AS depth, ARRAY[position] AS path
  FROM nodes WHERE id = 1
  UNION ALL
  SELECT n.id, n.parent_id, n.title, n.position, t.depth + 1, t.path || n.position
  FROM nodes n JOIN tree t ON n.parent_id = t.id
)
SELECT * FROM tree ORDER BY path, id;
```

## 提醒事項

任務卡片上可以設定「提醒時間」與「重複」（不重複／每天／每週／每月）。提醒有兩層機制：

1. **網頁開著時**：Agenda 頁（`/agenda`）每 60 秒自動查一次到期提醒，用瀏覽器桌面通知跳出。第一次使用要點頁面上的「啟用通知」按鈕允許權限。
2. **網頁關著時（推薦，才是真正可靠的提醒）**：`notifier.py` 是一支獨立腳本，直接查資料庫、用 macOS 原生通知跳出，不需要瀏覽器開著。搭配 launchd 排程每分鐘執行一次：

```sh
# 把 plist 複製到 LaunchAgents 並載入（一次性設定，之後開機自動生效）
cp launchd/com.orgtd.notifier.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.orgtd.notifier.plist

# 要停用時
launchctl unload ~/Library/LaunchAgents/com.orgtd.notifier.plist
rm ~/Library/LaunchAgents/com.orgtd.notifier.plist

# 手動測試腳本本身有沒有作用（不透過 launchd）
.venv/bin/python notifier.py
```

`launchd/notifier.log` / `launchd/notifier.err.log` 可查執行紀錄。這支腳本需要 PostgreSQL 一直在跑（`brew services start postgresql@17` 已經是開機自動啟動的背景服務）。

重複任務完成時不會真的關閉，而是把排程/截止日往後推一輪、狀態留在 NEXT（org-mode 的重複任務精神），適合「每週回顧」「每月繳費」這類週期性事項。

## 中文搜尋的限制

PostgreSQL 內建的全文搜尋（`to_tsvector`）不會斷中文詞，本專案用 `pg_trgm`（三元組模糊比對）做中文子字串搜尋，不是真正的語意斷詞。若未來想要更準的中文搜尋，可以研究安裝 `zhparser` 擴充套件。

## 目前進度

- [x] M0 骨架：專案結構、資料庫 schema（全部 model 一次建表）、Inbox 收集箱最小版、錯誤頁
- [x] M1 最小閉環：clarify（任務/筆記/將來也許）、Agenda（逾期／今日排程／NEXT 行動）、TODO 狀態機切換
- [x] M2 Projects + 標籤 + outline 樹：專案清單/詳情、遞迴 CTE 子樹、新增子任務、跨專案搬移（含循環防護）、卡點偵測（無 NEXT 行動標紅）、標籤新增/移除
- [x] M3 Calendar：月曆格檢視、白名單 year/month 驗證（非法月份 404）、SCHEDULED/DEADLINE 落格、跨年翻頁
- [x] M3.5 提醒：任務可設提醒時間與重複規則（每天/每週/每月）；Agenda 頁輪詢 + 桌面通知（頁面開著時）；`notifier.py` + launchd 每分鐘跑一次（頁面關著也能提醒，尚未啟用，見上方「提醒事項」段落自行 `launchctl load`）；重複任務完成時往後推一輪而非真的關閉
- [x] M4 Pomodoro：計時器頁（`pomodoro.js`，唯一即時互動 JS）綁定任務節點、完成/放棄記錄進 `pomodoro_sessions`、今日番茄數、專注→短休息/長休息自動輪替（每 4 顆長休息）
- [x] M5 Notes + 搜尋：筆記清單/詳情/編輯、pg_trgm 中文子字串模糊搜尋（標題+內文）、封存
- [x] M6 Weekly Review wizard：8 步引導式週回顧（清空收集箱/NEXT/WAITING/專案卡點偵測/Someday/未來一週行事曆/本週番茄統計/自由反思）、JSONB checklist 進度追蹤、以週一為單位、完成頁
- [x] M7 Dashboard：連續天數 streak（窗口函數 gaps-and-islands）、本週番茄聚合與專案時間分布、GTD 系統健康度總覽（收集箱/NEXT/WAITING/Someday/卡住的專案）、nav 徽章全面接上（收集箱/今日待辦/本週番茄）

**全部 8 個里程碑（M0–M7）已完成。** `orgtd` 現在是一套完整可日用的本機 GTD × org-mode 系統。
