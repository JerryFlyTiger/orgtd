# orgtd

GTD × org-mode 的時間與專案管理系統。多人帳號，資料同時存在 PostgreSQL
與 **org-mode 純文字檔**——網頁上編輯，也能直接用 Emacs 或 VSCode 打開
同一份檔案。

Flask 3 · PostgreSQL 17 · SQLAlchemy 2.0 · Alembic · 39 個測試

---

## 這個專案在解什麼問題

GTD 工具與 org-mode 各有各的好，但通常只能二選一：

- **GTD 的 SaaS 工具**（Todoist、Things）介面好用，但資料鎖在別人的雲端，
  匯出的格式也不是能拿來編輯的東西。
- **Emacs org-mode** 資料是自己的純文字，可以版控、可以 grep、二十年後
  還打得開，但沒有網頁介面，換一台電腦或想在手機上看就很麻煩。

orgtd 讓兩邊同時成立：網頁介面負責日常操作與統計，org 檔負責長期保存
與跨工具編輯。**同一份資料，兩個入口。**

---

## 三個關鍵設計決策

### 1. org 檔是事實來源，PostgreSQL 是衍生索引

這是整個系統最重要的取捨。

如果反過來（DB 為準、org 檔只是匯出），那麼你在 Emacs 裡的修改下一次同步
就會被覆蓋掉——「能用 Emacs 編輯」就是假的。所以方向必須是：檔案優先，
資料庫是它的索引。

Postgres 仍然做它真正擅長的事，這些全是唯讀路徑，不受影響：

| 能力 | 用到的技術 |
|---|---|
| outline 大綱樹展開 | `WITH RECURSIVE` CTE，用陣列 path 排序 |
| 中文子字串搜尋 | `pg_trgm` 三連字 GIN 索引 |
| agenda 查詢 | 部分索引（`WHERE archived_at IS NULL`），索引體積只含未封存節點 |
| 連續天數 streak | 窗口函數的 gaps-and-islands 解法 |
| 番茄鐘統計 | `date_trunc` 聚合 |

> 為什麼中文搜尋不用內建全文檢索：PostgreSQL 的 `to_tsvector` 不斷中文詞，
> 「專案管理」整串會變成一個 token，搜「專案」找不到。`pg_trgm` 做的是
> 三個字元一組的模糊比對，不需要詞典就能處理子字串。代價是它比對的是
> 字形而非語意，要更準得裝 `zhparser`。

### 2. 節點身分靠 org 的 `:ID:` 屬性，不靠標題或行號

每個節點在 org 檔的 `:PROPERTIES:` 抽屜裡帶一個 `:ID:`（就是 Emacs 內建
`org-id` 的用法）：

```org
* NEXT [#A] 買咖啡豆                                            :errand:
  SCHEDULED: <2026-09-08 Tue 09:00 +1w>
  :PROPERTIES:
  :ID: 0e4d38e2-3a1f-4c88-9b2e-7f1a5c6d8e90
  :ORGTD_KIND: task
  :END:
```

所以你在 Emacs 裡改標題、搬到別的位置、重新排序，回寫時仍然對得回同一列
資料——番茄鐘紀錄與提醒設定不會斷掉。這件事有測試守著
（`test_node_identity_survives_external_edit`）。

沒有 `:ID:` 的節點（你手動打的）會在匯入時自動配發一個。

### 3. 多租戶的 user_id 是必填位置參數，不是預設值

`queries.py` 裡每個函式都長這樣：

```python
def fetch_agenda(session, user_id):   # 不是 user_id=None
```

刻意不給預設值、也不從 `current_user` 隱式取用。漏帶時會立刻 `TypeError`
當掉，而不是安靜地把別人的資料查出來。**跨租戶洩漏應該是會當機的錯誤，
不能靠人記得加 `where`。**

同理，所有「用 id 取單一物件」的路徑都收斂到 `views/_scope.py` 的
`owned_node()`，它強制帶擁有者條件。原本單人版直接 `session.get(Node, id)`
——那在多人環境下是典型的 IDOR，把網址上的 id 換掉就能讀寫別人的東西。
`tests/test_tenant_isolation.py` 有 11 個測試從 HTTP 層驗證這件事。

---

## 帳號與登入

只做一種登入方式：**email + 密碼**。

- argon2id 雜湊（OWASP 現行首選），密碼最短 12 字元。依 NIST SP 800-63B
  的建議，長度優先於「大小寫加符號」那類複雜度規則——後者只會逼出
  `P@ssw0rd!` 這種好猜又難記的密碼。
- **CSRF**：所有 POST 走 Flask-WTF 的 token 驗證（JSON 請求走
  `X-CSRFToken` 標頭）。
- **Session**：Flask-Login，`session_protection="strong"`。

兩個刻意的安全處理：

- **帳號枚舉防護**：「帳號不存在」與「密碼錯誤」回傳完全相同的訊息；
  IDOR 的 404 也不區分「不存在」與「不屬於你」。否則攻擊者能靠回應差異
  探測出哪些帳號或 id 是真的。
- **未設密碼的帳號無法登入**：`password_hash` 允許為 NULL（舊單人資料
  遷移過來的站長帳號就是這個狀態），`verify_password()` 對 NULL 一律回
  `False`，不會因為「空密碼比對成功」而放行。

### 為什麼沒有第三方登入

Google、Apple、X 都評估過，結論是成本大於價值：

| | 費用 | 其他障礙 |
|---|---|---|
| Google | 免費 | 要在 Google Cloud Console 申請憑證、設定回呼網址 |
| Apple | **每年 99 美元**（Developer Program） | 不接受 `localhost` 回呼，必須先有已驗證的 HTTPS 網域；client secret 是每半年要重簽的 JWT |
| X | **每月 200 美元**（Basic 層） | 免費層多數端點限制到 24 小時 1 次請求，而登入後必須呼叫 `GET /2/users/me` 才能識別使用者——等於第二個人登入就失敗 |

Google 那條唯一的障礙只是申請流程，但為了一個自架的個人系統多接一個
外部相依與一組要保管的密鑰，不划算。要加回來的話，
`migrations/versions/b2c3d4e5f6a7_drop_oauth_accounts.py` 的 `downgrade()`
就是現成的建表腳本。

## org 資料夾

每位使用者一個資料夾，裡面是 org-mode GTD 社群慣例的檔案配置：

```
~/org/                  ← 建議路徑（見下方說明）
├── inbox.org           收集箱
├── projects.org        專案（含底下的任務子樹）
├── notes.org           筆記
├── someday.org         將來也許
├── archive.org         已封存
└── .orgtd/             同步指紋，Emacs 不會誤收（點開頭）
```

**為什麼建議叫 `~/org`**：這是 Emacs 變數 `org-directory` 的預設值，
`org-agenda-files` 的慣例也指向那裡。用這個名字，Emacs 使用者的既有設定
不用改就吃得到。設定頁可以改成別的路徑。

### 在 Emacs 裡用

```elisp
(setq org-directory "~/org")
(setq org-agenda-files (directory-files-recursively "~/org" "\\.org$"))
```

`C-c a` 就會看到 orgtd 產生的所有排程與截止項目。

### 在 VSCode 裡用

裝 [Org Mode 擴充套件](https://marketplace.visualstudio.com/items?itemName=vscode-org-mode.org-mode)，
直接開資料夾即可。沒裝擴充也能當純文字編輯，格式不會壞。

### 兩邊改動怎麼合

同步指紋存在 `.orgtd/state.json`。設定頁有兩個按鈕：

- **從檔案匯入**：偵測到外部改動時，以檔案內容為準回寫資料庫
- **重新產生檔案**：以資料庫為準重寫 org 檔

也可以走指令列：

```sh
.venv/bin/python manage.py import your@email.com
.venv/bin/python manage.py export your@email.com
```

檔案裡消失的節點會被**封存**而不是硬刪——手滑刪掉一段還救得回來。

### 遠端使用者的資料夾在哪

這點值得講清楚：如果你把 orgtd 架在一台伺服器上給別人用，那些 org 檔是在
**伺服器的磁碟上**，遠端使用者沒辦法用自己電腦的 Emacs 直接打開。所以：

- **自架單人**（`ORGTD_ALLOW_CUSTOM_ORG_DIR=1`）：資料夾就在你自己的機器上，
  設成 `~/org`，Emacs 直接開，這是完整體驗。
- **對外多人站台**（設 `0`）：資料夾由系統配置在 `<ORG_ROOT>/<uuid>/`，
  使用者透過設定頁的 **下載 .zip** 取得檔案。

多人站台**不開放**使用者自填路徑，因為那填的是伺服器上的路徑，等同讓任何
註冊者對伺服器檔案系統任意寫入。這個限制在程式裡是硬性的，不只是介面上藏起來。

---

## 安裝

```sh
# 1. 系統依賴
brew install postgresql@17 python@3.12
brew services start postgresql@17
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"

# 2. 資料庫
createdb orgtd

# 3. Python 環境
cd ~/My_Projects/orgtd
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. 設定檔
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # 貼進 ORGTD_SECRET_KEY

# 5. 建表
.venv/bin/alembic upgrade head

# 6. 建第一個帳號
.venv/bin/python manage.py create-user your@email.com "你的名字"
```

### 設定項目

`.env`（已列入 `.gitignore`，不會進版控）：

| 變數 | 說明 |
|---|---|
| `ORGTD_SECRET_KEY` | **必填**。session 簽章密鑰，沒設定且非 debug 模式會直接拒絕啟動 |
| `ORGTD_DATABASE_URL` | 連線字串 |
| `ORGTD_DEBUG` | 預設 `0`。對外部署務必保持關閉 |
| `ORGTD_ORG_ROOT` | org 資料夾根目錄，預設 `~/orgtd-data` |
| `ORGTD_ALLOW_CUSTOM_ORG_DIR` | 自架單人設 `1`，對外站台設 `0` |
| `ORGTD_ALLOW_REGISTRATION` | 是否開放註冊 |
| `ORGTD_NOTIFY_EMAIL` | 桌面提醒發給誰，留空取最早建立的帳號 |

---

## 啟動

```sh
# 開發
.venv/bin/python app.py            # http://127.0.0.1:5001

# 正式（gunicorn，不是 Flask 內建伺服器）
.venv/bin/gunicorn -w 4 -b 127.0.0.1:5001 "app:create_app()"
```

Flask 內建的 Werkzeug 伺服器是單執行緒開發用的，而且 `debug=True` 時它的
除錯器允許在瀏覽器裡執行任意 Python——對外開一個 port 就是完整的遠端執行
漏洞。這個專案的 debug 現在由 `ORGTD_DEBUG` 控制，**預設關閉**。

### 用雙擊圖示啟動

`gui/` 有三個 AppleScript 編譯的 `.app`（啟動／關閉／開網頁），可拖到 Dock。
用 `gui/build.sh` 從 `.applescript` 原始碼重新編譯（`osacompile` 是 macOS 內建，
不用額外裝東西）。

雙擊啟動會設 `ORGTD_NO_RELOAD=1` 關掉 reloader，讓伺服器只有單一 process
——reloader 會多 fork 一個子行程，只殺父行程會殘留子行程繼續佔用 port。

---

## 測試

```sh
createdb orgtd_test
ORGTD_DATABASE_URL="postgresql+psycopg://$(whoami)@localhost:5432/orgtd_test" \
  .venv/bin/alembic upgrade head
ORGTD_DATABASE_URL="postgresql+psycopg://$(whoami)@localhost:5432/orgtd_test" \
  .venv/bin/python -m pytest tests/ -q
```

39 個測試，分四組：

| 檔案 | 守的是什麼 |
|---|---|
| `test_tenant_isolation.py` | 11 項。B 使用者讀不到也改不到 A 的任何東西 |
| `test_org_sync.py` | 11 項。外部編輯真的能回寫，且節點身分不斷 |
| `test_org_roundtrip.py` | 9 項。org 解析／輸出無損，含中文標題、標籤、重複規則 |
| `test_auth.py` / `test_csrf.py` | 8 項。登入、存取控制、CSRF |

幾個測試是回歸測試，對應開發時真的踩到的坑：

- `test_title_without_state_is_not_swallowed`——標題「TODOs 清單整理」
  不可被誤判成 TODO 狀態
- `test_custom_dir_rejects_dangerous_paths`——macOS 的 `/etc` 是
  `/private/etc` 的符號連結，早先用字面前綴比對的黑名單會被繞過，
  後來改成「必須在家目錄內」的正面表列
- `test_emacs_style_localised_daynames_parse`——Emacs 依語系會把星期寫成
  「週一」，解析器不能假設是英文縮寫
- `test_fresh_directory_reports_no_external_changes`——建立資料夾時忘了
  登記檔案指紋，導致每個新帳號一進設定頁就看到五個檔案全掛著
  「外部已修改」的假警報

---

## 提醒

兩層機制：

1. **網頁開著**：Agenda 頁每 60 秒輪詢，瀏覽器桌面通知
2. **網頁關著**：`notifier.py` 由 launchd 每分鐘跑一次，走 macOS 原生通知

```sh
cp launchd/com.orgtd.notifier.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.orgtd.notifier.plist
```

兩邊共用「取出即標記已通知」的邏輯，誰先查到就算數，不會重複通知。

> 桌面通知只對坐在這台機器前面的人有意義，所以 `notifier.py` 預設只處理
> 機器主人自己的提醒。改成掃全站會把其他使用者的任務標題彈到主人桌面上
> ——那是資料外洩。

重複任務（`+1d` / `+1w` / `+1m`）完成時不會關閉，而是把排程與截止日往後推
一輪、狀態留回 NEXT，這是 org-mode 重複任務的語意。

---

## 管理指令

```sh
.venv/bin/python manage.py list-users
.venv/bin/python manage.py create-user <email> [顯示名稱]
.venv/bin/python manage.py set-password <email>
.venv/bin/python manage.py export <email>     # DB → org 檔
.venv/bin/python manage.py import <email>     # org 檔 → DB
```

---

## 資料模型

一切皆節點。`nodes` 一張表用 `kind` 區分 inbox / project / task / note /
someday，`parent_id` 自關聯構成 outline 大綱樹。

GTD 的 clarify 動作因此只是一次 `UPDATE kind, parent_id, todo_state`，
不用搬表——這對應 GTD 流程本身的流動性：一個收集箱項目可能變成任務、
變成專案、變成參考資料，或者直接丟掉。

多租戶改造時修掉的兩個地雷（原本是單人設計）：

- `tags.name` 原本全域唯一 → 第二個人建同名標籤會失敗
- `weekly_reviews.week_start` 原本全域唯一 → 第二個人做同一週的回顧會撞主鍵

兩者都改成 `(user_id, X)` 的複合唯一鍵，各有一個測試守著。

完整設計理念見 [`PLAN.md`](PLAN.md)。

---

## 專案結構

```
app.py            應用工廠、blueprint 註冊、CSRF 與登入初始化
config.py         環境變數集中處，程式碼裡不寫死任何密鑰
db.py             engine / session / Base
models.py         SQLAlchemy 模型與索引定義
queries.py        跨 view 共用的進階查詢（全部強制帶 user_id）
security.py       argon2 密碼雜湊、Flask-Login 設定
orgfiles.py       org 純文字的解析與輸出、資料夾管理
orgsync.py        DB ⇄ org 檔的雙向同步引擎
manage.py         管理指令列
views/            9 個功能 blueprint + auth + settings + _scope 取用輔助
templates/        Jinja2 模板
static/           CSS 與兩支 JS（番茄鐘計時、提醒輪詢）
migrations/       Alembic 遷移
tests/            39 個測試
launchd/          提醒排程的 plist
gui/              macOS 雙擊啟動的 AppleScript
```
