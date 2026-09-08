# orgtd

GTD × org-mode 的時間與專案管理系統。多人帳號、網頁介面，資料可一鍵
匯出成 **org-mode 純文字檔**，下載到自己電腦用 Emacs 或 VSCode 打開。

Flask 3 · PostgreSQL 17 · SQLAlchemy 2.0 · Alembic · 99 個測試

---

## 這個專案在解什麼問題

GTD 工具與 org-mode 各有各的好，通常只能二選一：

- **GTD 的 SaaS 工具**（Todoist、Things）介面好用，但資料鎖在別人的雲端，
  匯出的格式往往不是能直接拿來編輯的東西。
- **Emacs org-mode** 資料是自己的純文字，可以版控、可以 grep、二十年後
  還打得開，但沒有網頁介面。

orgtd 的取捨是：**網頁介面負責日常操作與統計，隨時能把資料以 org 格式
帶走**。匯出的不是專有格式的備份檔，是可以直接編輯、直接餵給
`org-agenda` 的 org 檔。

---

## 三個關鍵設計決策

### 1. 一切皆節點

`nodes` 一張表用 `kind` 區分 inbox / project / task / note / someday，
`parent_id` 自關聯構成 org-mode 式的 outline 大綱樹。

GTD 的 clarify 動作因此只是一次 `UPDATE kind, parent_id, todo_state`，
不用搬表——這對應 GTD 流程本身的流動性：一個收集箱項目可能變成任務、
變成專案、變成參考資料，或者直接丟掉。

這讓 PostgreSQL 能發揮它真正擅長的部分：

| 能力 | 用到的技術 |
|---|---|
| outline 大綱樹展開 | `WITH RECURSIVE` CTE，用陣列 path 排序 |
| 中文子字串搜尋 | `pg_trgm` 三連字 GIN 索引 |
| agenda 查詢 | 部分索引（`WHERE archived_at IS NULL`），索引只含未封存節點 |
| 連續天數 streak | 窗口函數的 gaps-and-islands 解法 |
| 番茄鐘統計 | `date_trunc` 聚合 |

> 為什麼中文搜尋不用內建全文檢索：PostgreSQL 的 `to_tsvector` 不斷中文詞，
> 「專案管理」整串會變成一個 token，搜「專案」找不到。`pg_trgm` 做的是
> 三個字元一組的模糊比對，不需要詞典就能處理子字串。代價是它比對字形
> 而非語意，要更準得裝 `zhparser`。

### 2. org 檔是單向匯出，不是同步

資料庫是唯一的事實來源。按下匯出才即時算出 org 檔，**伺服器不留副本**。

一開始做的是雙向同步——org 檔長期存在磁碟上，外部編輯能回寫資料庫。
砍掉的理由是複雜度不成比例：要替每個使用者保管資料夾、記錄檔案指紋、
偵測外部改動、處理兩邊同時修改的衝突，還要擋住「使用者填的路徑其實是
伺服器上的路徑」這種任意寫入破口。

而它真正要滿足的需求只是「我想用 Emacs 開自己的資料」——下載一份就夠了。
砍掉之後少了一個模組、五個磁碟路徑相關的設定，以及一整類同步 bug。

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

三個刻意的安全處理：

- **帳號枚舉防護**：「帳號不存在」與「密碼錯誤」回傳完全相同的訊息；
  IDOR 的 404 也不區分「不存在」與「不屬於你」。否則攻擊者能靠回應差異
  探測出哪些帳號或 id 是真的。
- **未設密碼的帳號無法登入**：`password_hash` 允許為 NULL（舊單人資料
  遷移過來的站長帳號就是這個狀態），`verify_password()` 對 NULL 一律回
  `False`，不會因為「空密碼比對成功」而放行。
- **改 email 要輸入目前密碼**：email 是帳號的主要識別，session 若被竊，
  改掉 email 等於接管帳號，所以不能只靠「已登入」就放行。

### email 一律轉小寫存

`email_validator` 的 `normalized` 只把網域轉小寫、保留使用者名稱的大小寫
（RFC 上 local part 確實區分大小寫），但這個專案所有查詢都是 `.lower()`
比對——兩邊不一致的話，用 `Jerry@x.com` 註冊完就再也登不進去。所以
`security.normalize_email()` 驗完格式後把整串轉小寫，寫入與查詢共用同一
個基準。實務上主流信箱供應商也都不分大小寫。

驗證刻意關掉 `check_deliverability`：那會做 DNS 查詢，開發時常常離線，
而且一次網路往返會讓註冊表單莫名卡好幾秒；格式對不對不需要問 DNS。
代價是 `jerry@local` 這種沒有點的網域會被拒絕——所以登入端在正規化失敗時
會退回單純的去空白轉小寫，早期建立的帳號才不會被自己的資料鎖在門外。

改掉程式碼救不了已經存進資料庫的舊值：Alembic 不會重跑已套用的 revision。
既有帳號的正規化由 `d4e5f6a7b8c9` 這支資料修正 migration 處理，它若發現
兩個帳號正規化後會撞在一起，會直接失敗並要求人工決定，而不是自動合併或
刪掉其中一方——那是不可逆的資料決策。

### 查詢鍵只有一個來源

`emails.lookup_key()` 是唯一該用來查詢既有帳號的函式，登入、`manage.py`
與資料修正 migration 全部走它。這件事被踩過一次：三邊各自寫了「去空白 +
轉小寫（+ 有時候 NFC）」，看起來等價，實際上不是——`validate_email()` 對
網域還會做 IDNA/UTS-46 相容映射（全形 `ｅｘａｍｐｌｅ` → `example`），
自製的 NFC 版本不會。於是那支「修正資料」的 migration 反而把帳號改成
登不進去。一致性要由結構保證，不能靠人記得同步三個地方。

### 忘記密碼：一次性救援碼

註冊時會發 10 組一次性救援碼，只顯示那一次（資料庫只存雜湊）。忘記密碼時
在登入頁點「忘記密碼」，輸入 email 加任一組碼就能直接重設。每組用一次，
可以隨時到設定頁重新產生一批（舊的全部作廢）。

**為什麼不做「寄重設連結到信箱」**：這個系統沒有寄信能力，自架環境要接
SMTP 等於多一組要保管的憑證與一個會壞的外部相依。救援碼把恢復能力交還給
使用者自己保管，不需要任何外部服務。

幾個實作上的取捨：

- **救援碼用 SHA-256，不用 argon2**。密碼要用慢雜湊，是因為人選的密碼熵
  很低，必須讓每次猜測都昂貴；救援碼是程式產生的 124 位元隨機值，暴力
  搜尋在物理上不可行。而且確定性雜湊可以直接用索引查到那一列，argon2
  每次加鹽就得把使用者所有未使用的碼逐一驗過。
- **刻意不做嘗試次數限制**，依據就是上面那個熵。有一支測試
  （`test_codes_have_enough_entropy_to_skip_rate_limiting`）守著這個前提，
  哪天有人把碼改短會先被擋下來。
- **字母表排除 0/O/1/I/L**。這些碼是要用手抄或手打的，可讀性比字母表大小
  重要——少 7 個字元只讓每字元少約 0.5 bit。輸入時大小寫、空白、連字號
  都容忍。
- **明碼絕不進 session**。註冊與重新產生都是直接渲染 POST 的回應，不轉址。
  Flask 的 session cookie 只有簽章、沒有加密，內容是任何拿到 cookie 的人
  都讀得出來的。
- **重設時先驗新密碼、再消耗救援碼**。順序反過來的話，新密碼打太短就白白
  燒掉一組碼。兩支測試釘著這個順序。
- **消耗是單一敘述的原子操作**（`UPDATE ... WHERE used_at IS NULL ... RETURNING`），
  不是「先 SELECT 出來、再把 used_at 寫回去」。後者在併發下守不住單次使用：
  兩個請求的 SELECT 都會在對方 commit 之前讀到 `used_at IS NULL`，雙方都
  判定成功。這不是理論上的窄窗——冷讀指出後實測 8 條併發打同一組碼，
  **每一輪都有 6 到 8 條同時消耗成功**。有一支多執行緒的回歸測試守著。
- **明碼頁回 `Cache-Control: no-store`**。這是全站唯一一個回應本文含長期
  有效機密的頁面，否則公用電腦上按「上一頁」就能把碼叫回來。
- **查無此帳號時也照樣跑一次驗證**。寫成
  `user is None or not recovery.consume(...)` 的話，Python 的短路會讓
  「查無此帳號」跳過雜湊與查詢，明顯比「帳號存在但碼錯」快——訊息藏好了，
  卻從耗時洩漏出去。守著這條的測試斷言的是「`consume` 有沒有被呼叫」這個
  結構性質，而不是量測時間：計時測試會偶發失敗，斷言結構不會。

連救援碼都遺失時，還有終端機這條路：
`manage.py recovery-codes <email>`。能跑這個指令代表你有這台機器的存取權，
本來就等同擁有這個系統的一切。

### 為什麼沒有第三方登入

Google、Apple、X 都評估過，結論是成本大於價值：

| | 費用 | 其他障礙 |
|---|---|---|
| Google | 免費 | 要在 Google Cloud Console 申請憑證、設定回呼網址 |
| Apple | **每年 99 美元**（Developer Program） | 不接受 `localhost` 回呼，必須先有已驗證的 HTTPS 網域；client secret 是每半年要重簽的 JWT |
| X | **每月 200 美元**（Basic 層） | 免費層多數端點限制到 24 小時 1 次請求，而登入後必須呼叫 `GET /2/users/me` 才能識別使用者——等於第二個人登入就失敗 |

Google 那條唯一的障礙只是申請流程，但為了一個自架的個人系統多接一個
外部相依與一組要保管的密鑰，不划算。

---

## 設定頁

- 改顯示名稱
- 改登入用的 email（需輸入目前密碼）
- 改密碼（有即時的長度與一致性提示）
- 重新產生救援碼（需輸入目前密碼）
- 番茄鐘時長參數
- 匯出 org 檔（見下）

---

## 匯出 org 檔

設定頁可以整包下載 `.zip`，或單獨下載某一個檔案。內容依 org-mode GTD
社群的慣例分成五份：

```
inbox.org      收集箱
projects.org   專案（含底下的任務子樹）
notes.org      筆記
someday.org    將來也許
archive.org    已封存
```

產出的是標準 org-mode 格式：

```org
* NEXT [#A] 買咖啡豆                                            :errand:
  SCHEDULED: <2026-09-08 Tue 09:00 +1w>
  :PROPERTIES:
  :ID: 0e4d38e2-3a1f-4c88-9b2e-7f1a5c6d8e90
  :ORGTD_KIND: task
  :END:
```

`:ID:` 用的是 Emacs 內建 `org-id` 的欄位名，而且**每次匯出對同一個節點
都給同一個值**——它存在資料庫裡，不是匯出時臨時產生的。所以你在 Emacs
裡建立的 org-id 連結不會因為重新匯出一次就失效。

### 在 Emacs 裡用

```elisp
(setq org-agenda-files (directory-files-recursively "~/org" "\\.org$"))
```

`C-c a` 就會看到匯出的所有排程與截止項目。

### 在 VSCode 裡用

裝 [Org Mode 擴充套件](https://marketplace.visualstudio.com/items?itemName=vscode-org-mode.org-mode)
後直接開資料夾。沒裝擴充當純文字讀也不會亂。

### 也可以走指令列

```sh
.venv/bin/python manage.py export your@email.com ~/org
```

> 匯出是**單向**的：在 Emacs 或 VSCode 裡的改動不會回寫到網站。

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
漏洞。這個專案的 debug 由 `ORGTD_DEBUG` 控制，**預設關閉**。

### 用雙擊圖示啟動

`gui/` 有三個 AppleScript 編譯的 `.app`（啟動／關閉／開網頁），可拖到 Dock。
用 `gui/build.sh` 從 `.applescript` 原始碼重新編譯（`osacompile` 是 macOS
內建，不用額外裝東西）。

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

99 個測試，分六組：

| 檔案 | 守的是什麼 |
|---|---|
| `test_org_export.py` | 25 項。匯出的真的是 Emacs 讀得懂的 org 格式，且只含自己的資料 |
| `test_recovery.py` | 33 項。救援碼與忘記密碼流程每一種「不該成功」的情況 |
| `test_change_email.py` | 15 項。改 email 的每一種「不該成功」的情況 |
| `test_auth.py` | 13 項。註冊、登入、存取控制、email 正規化一致性 |
| `test_tenant_isolation.py` | 11 項。B 使用者讀不到也改不到 A 的任何東西 |
| `test_csrf.py` | 2 項。沒帶 token 的 POST 一律拒絕 |

幾個是回歸測試，對應開發時真的踩到的坑：

- `test_newline_in_title_is_flattened`——標題裡的換行會把一個 headline
  拆成兩行，破壞整份檔案的結構
- `test_midnight_scheduled_omits_time`——午夜的排程不該印出 `00:00`，
  org 的慣例是純日期
- `test_org_id_is_stable_across_exports`——`:ID:` 若每次匯出都重算，
  使用者在 Emacs 建立的 org-id 連結就會失效
- `test_filename_must_be_on_the_whitelist`——下載單一檔案時檔名走白名單
  比對而非路徑組合，從根本上沒有 `../` 穿越的餘地
- `test_email_is_stored_lowercase`——email 存進去若保留大小寫，之後用
  小寫查就找不到人，等於使用者改完 email 把自己鎖在門外
- `test_unicode_is_normalised_consistently_between_register_and_login`——
  註冊端會做 Unicode NFC 正規化而登入端只做 `.lower()`，用分解形式
  （`e` + 組合重音）註冊的人會永遠登不回去，且沒有忘記密碼流程可以自救
- `test_race_on_unique_email_is_handled_gracefully`——唯一性檢查到 commit
  之間的空窗由資料庫約束擋下，但沒接住 `IntegrityError` 的話使用者看到的
  是 500 而不是「這個 email 已經被使用了」
- `test_legacy_account_with_nonstandard_email_can_still_log_in`——早期建立
  的帳號 email 未必通過現行格式規則，登入端少了那條退路，這些人會被自己
  的資料永久鎖在門外
- `test_register_and_login_survive_idna_domain_mapping`——這支用全形網域
  走完整的註冊→登出→登入。其餘登入測試全是純 ASCII，而純 ASCII 下
  `lookup_key()` 跟天真的 `.strip().lower()` 行為完全一樣，等於防線在
  使用者真正的入口沒被測到
- `test_short_password_does_not_burn_the_code`——重設密碼時若先消耗救援碼
  再驗密碼，新密碼打太短就白白燒掉一組
- `test_settings_page_title_is_not_polluted`——同一個坑踩過兩次：用
  `str.replace("{% endblock %}", ...)` 插入區塊時沒限制次數，而模板有兩個
  `endblock`，整段 HTML 被插進 `<title>` 裡
- `test_concurrent_use_of_one_code_only_succeeds_once`——8 條執行緒同時打
  同一組救援碼，只有一條能成功
- `test_backslash_in_display_name_does_not_break_the_page`——Jinja 的自動
  跳脫是給 HTML 用的、不處理反斜線，顯示名稱以 `\` 結尾就會把 JS 字串的
  收尾引號跳脫掉，整段 script SyntaxError、按鈕靜默失效
- `test_settings_password_change_uses_the_shared_minimum`——密碼長度下限
  若在前端與各個後端路徑各自硬編碼，調高時前端會承諾一件後端沒在擋的事

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

重複任務（`+1d` / `+1w` / `+1m`）完成時不會關閉，而是把排程與截止日往後
推一輪、狀態留回 NEXT，這是 org-mode 重複任務的語意。

---

## 管理指令

```sh
.venv/bin/python manage.py list-users
.venv/bin/python manage.py create-user <email> [顯示名稱]
.venv/bin/python manage.py set-password <email>
.venv/bin/python manage.py recovery-codes <email>
.venv/bin/python manage.py export <email> <目標資料夾>
```

---

## 多租戶改造時修掉的地雷

原本是單人設計，改成多人時有兩個全域唯一鍵會直接撞車：

- `tags.name` 原本全域唯一 → 第二個人建同名標籤會失敗
- `weekly_reviews.week_start` 原本全域唯一 → 第二個人做同一週的回顧會撞主鍵

兩者都改成 `(user_id, X)` 的複合唯一鍵，各有一個測試守著。索引也全部重建，
把 `user_id` 移到最左欄——否則多租戶篩選吃不到索引。

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
recovery.py       一次性救援碼的產生與驗證
emails.py         email 驗證與正規化（全站唯一來源，不相依框架）
orgfiles.py       org 純文字匯出（單向，記憶體內完成）
manage.py         管理指令列
views/            9 個功能 blueprint + auth + settings + _scope 取用輔助
templates/        Jinja2 模板
static/           CSS 與三支 JS（番茄鐘計時、提醒輪詢、密碼提示）
migrations/       Alembic 遷移
tests/            99 個測試
launchd/          提醒排程的 plist
gui/              macOS 雙擊啟動的 AppleScript
```
