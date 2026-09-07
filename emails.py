"""email 的驗證與正規化——全站唯一的來源。

刻意不相依 Flask 或任何框架：migration 也要用它，而 migration 應該能在
最精簡的環境下跑起來。

為什麼要獨立成一個模組：先前登入、manage.py、migration 各自寫了一份
「去空白 + 轉小寫（+ 有時候 NFC）」，三份必然漂移，實際上也真的漂移了——
migration 用 `unicodedata.normalize("NFC", ...)`，但 validate_email() 對網域
還會做 IDNA/UTS-46 相容映射（例如把全形 ｅｘａｍｐｌｅ 轉成 example），
兩者對同一個 email 算出不同的鍵，於是那支「修正資料」的 migration 反而
把帳號改成登不進去。一致性要由結構保證，不能靠人記得同步。
"""

from email_validator import EmailNotValidError, validate_email

EMAIL_MAX_LENGTH = 255


def normalize_email(raw: str) -> tuple[str | None, str | None]:
    """驗證並正規化 email，回傳 (email, 錯誤訊息)，兩者必有一為 None。

    用於**接受新輸入**的場合（註冊、變更 email），格式不合就該擋下來。
    查詢既有帳號請改用 lookup_key()。

    兩個刻意的選擇：

    * `check_deliverability=False`——不做 DNS 查詢。開發時常常離線，而且
      一次網路往返會讓註冊表單莫名卡住好幾秒；格式正確與否不需要問 DNS。
    * 整串轉小寫存，而不是直接用 validate_email 的 `normalized`。後者只把
      網域轉小寫、保留使用者名稱的大小寫（RFC 上 local part 確實區分大小寫），
      但這個專案所有查詢都是小寫比對——兩邊不一致的話，用 `Jerry@x.com`
      註冊完就再也登不進去。實務上主流信箱供應商也都不分大小寫。
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "請填寫 email。"
    if len(raw) > EMAIL_MAX_LENGTH:
        return None, f"email 不能超過 {EMAIL_MAX_LENGTH} 個字元。"
    try:
        result = validate_email(raw, check_deliverability=False)
    except EmailNotValidError:
        return None, "email 格式不正確。"
    return result.normalized.lower(), None


def lookup_key(raw: str) -> str:
    """把使用者輸入換算成拿去比對 users.email 的鍵。

    這是**唯一**該用來查詢既有帳號的函式：登入、manage.py、以及修正既有
    資料的 migration 全部走這裡，三邊算出來的值才保證一致。

    格式不合現行規則時退回單純的去空白轉小寫——不驗格式是刻意的：早期
    建立的帳號（migration 產生的站長帳號、或還沒有格式檢查時註冊的）未必
    通過現在的規則，擋下來等於把人鎖在自己的資料外面，而這個系統沒有忘記
    密碼流程可以自救。這條退路也刻意不做 NFC：那些帳號當初就是以原始位元
    存進去的，多做一次正規化反而對不上。
    """
    normalised, error = normalize_email(raw)
    return (raw or "").strip().lower() if error else normalised
