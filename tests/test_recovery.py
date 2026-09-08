"""救援碼與忘記密碼流程。

這是帳號恢復途徑，設計錯誤等同帳號接管漏洞，所以重點放在
「不該成功的情況有沒有確實擋下」。
"""

import json
import re

import pytest
from sqlalchemy import select
from sqlalchemy import text as sa_text

import recovery
from conftest import PASSWORD, csrf_from, login
from db import SessionLocal
from models import RecoveryCode, User
from security import verify_password

NEW_PASSWORD = "a-brand-new-passphrase"


def _codes_from(html):
    return re.findall(r"<code>([A-Z0-9]{5}(?:-[A-Z0-9]{5}){4})</code>", html)


def _register(client, email="rec@example.com", password="a-long-enough-passphrase"):
    return client.post(
        "/register",
        data={
            "email": email,
            "display_name": "救援測試",
            "password": password,
            "password_confirm": password,
            "csrf_token": csrf_from(client, "/register"),
        },
    )


def _reset(client, email, code, password=NEW_PASSWORD, confirm=None):
    return client.post(
        "/forgot-password",
        data={
            "email": email,
            "recovery_code": code,
            "new_password": password,
            "new_password_confirm": confirm if confirm is not None else password,
            "csrf_token": csrf_from(client, "/forgot-password"),
        },
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# 產生與格式
# ---------------------------------------------------------------------------


def test_registration_issues_codes_and_shows_them_once(client):
    html = _register(client).get_data(as_text=True)
    codes = _codes_from(html)
    assert len(codes) == recovery.CODE_COUNT
    assert len(set(codes)) == recovery.CODE_COUNT, "不該出現重複的碼"


def test_plaintext_codes_are_never_stored(client):
    codes = _codes_from(_register(client).get_data(as_text=True))
    with SessionLocal() as session:
        rows = session.scalars(select(RecoveryCode)).all()
        stored = {r.code_hash for r in rows}
        assert len(rows) == recovery.CODE_COUNT
        for code in codes:
            assert code not in stored, "資料庫不該存明碼"
            assert recovery.hash_code(code) in stored


def test_codes_have_enough_entropy_to_skip_rate_limiting():
    """刻意不做嘗試次數限制，前提是碼本身不可暴力破解。

    這條斷言是那個設計決策的守衛：哪天有人把碼改短，這裡會先擋下來。
    """
    assert recovery.CODE_ENTROPY_BITS >= 100


@pytest.mark.parametrize("mangle", [
    lambda c: c.lower(),
    lambda c: c.replace("-", ""),
    lambda c: f"  {c}  ",
    lambda c: c.replace("-", " "),
    lambda c: c.lower().replace("-", ""),
])
def test_canonical_tolerates_how_people_type_it_back(mangle):
    """手抄回來的碼不該因為大小寫或連字號而被拒絕。"""
    code = recovery.generate_code()
    assert recovery.canonical(mangle(code)) == code


def test_alphabet_excludes_confusable_characters():
    for ch in "01OIL":
        assert ch not in recovery.ALPHABET, f"{ch} 手抄時容易看錯"


# ---------------------------------------------------------------------------
# 使用
# ---------------------------------------------------------------------------


def test_code_works_once_then_is_dead(client, make_user):
    uid, _ = make_user("once@example.com")
    with SessionLocal() as session:
        codes = recovery.issue(session, uid)

    with SessionLocal() as session:
        assert recovery.consume(session, uid, codes[0]) is True
        assert recovery.consume(session, uid, codes[0]) is False, "同一組不該能用第二次"
        assert recovery.unused_count(session, uid) == recovery.CODE_COUNT - 1


def test_wrong_code_is_rejected(client, make_user):
    uid, _ = make_user("wrong@example.com")
    with SessionLocal() as session:
        recovery.issue(session, uid)
        for bad in ["", "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", "too-short", "x" * 40]:
            assert recovery.consume(session, uid, bad) is False
        assert recovery.unused_count(session, uid) == recovery.CODE_COUNT


def test_another_users_code_does_not_work(client, make_user):
    a_id, _ = make_user("a@example.com")
    b_id, _ = make_user("b@example.com")
    with SessionLocal() as session:
        a_codes = recovery.issue(session, a_id)
        recovery.issue(session, b_id)
        assert recovery.consume(session, b_id, a_codes[0]) is False
        assert recovery.unused_count(session, a_id) == recovery.CODE_COUNT


def test_reissue_invalidates_the_old_batch(client, make_user):
    uid, _ = make_user("reissue@example.com")
    with SessionLocal() as session:
        old = recovery.issue(session, uid)
    with SessionLocal() as session:
        new = recovery.issue(session, uid)
        assert recovery.unused_count(session, uid) == recovery.CODE_COUNT
        assert recovery.consume(session, uid, old[0]) is False, "舊碼應已作廢"
        assert recovery.consume(session, uid, new[0]) is True


# ---------------------------------------------------------------------------
# 忘記密碼的完整流程
# ---------------------------------------------------------------------------


def test_forgot_password_end_to_end(client):
    codes = _codes_from(_register(client).get_data(as_text=True))
    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    resp = _reset(client, "rec@example.com", codes[0])
    assert "密碼已重設" in resp.get_data(as_text=True)

    login(client, "rec@example.com", NEW_PASSWORD)
    assert client.get("/").status_code == 200, "應能用新密碼登入"


def test_used_code_cannot_reset_again(client):
    codes = _codes_from(_register(client).get_data(as_text=True))
    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    _reset(client, "rec@example.com", codes[0])
    resp = _reset(client, "rec@example.com", codes[0], password="yet-another-passphrase")
    assert "救援碼不正確" in resp.get_data(as_text=True)

    login(client, "rec@example.com", NEW_PASSWORD)
    assert client.get("/").status_code == 200, "密碼應維持第一次重設的那組"


def test_wrong_code_leaves_password_untouched(client, make_user):
    uid, _ = make_user("intact@example.com")
    with SessionLocal() as session:
        recovery.issue(session, uid)

    resp = _reset(client, "intact@example.com", "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA")
    assert "救援碼不正確" in resp.get_data(as_text=True)
    with SessionLocal() as session:
        user = session.get(User, uid)
        assert verify_password(user.password_hash, PASSWORD), "原密碼不該被動到"


def test_unknown_email_gives_the_same_message(client, make_user):
    """訊息若有差異，就成了「這個 email 有沒有註冊過」的探測管道。"""
    uid, _ = make_user("known@example.com")
    with SessionLocal() as session:
        recovery.issue(session, uid)

    a = _reset(client, "known@example.com", "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA")
    b = _reset(client, "nobody@example.com", "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA")
    assert "email 或救援碼不正確" in a.get_data(as_text=True)
    assert "email 或救援碼不正確" in b.get_data(as_text=True)


def test_short_password_does_not_burn_the_code(client):
    """密碼沒通過驗證時，救援碼必須原封不動——不然打錯一次就少一組。"""
    codes = _codes_from(_register(client).get_data(as_text=True))
    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    resp = _reset(client, "rec@example.com", codes[0], password="short")
    assert "至少" in resp.get_data(as_text=True)

    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == "rec@example.com"))
        assert recovery.unused_count(session, user.id) == recovery.CODE_COUNT

    # 同一組碼仍然有效
    assert "密碼已重設" in _reset(client, "rec@example.com", codes[0]).get_data(as_text=True)


def test_mismatched_confirmation_does_not_burn_the_code(client):
    codes = _codes_from(_register(client).get_data(as_text=True))
    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    resp = _reset(client, "rec@example.com", codes[0], confirm="something-else-entirely")
    assert "不一致" in resp.get_data(as_text=True)
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == "rec@example.com"))
        assert recovery.unused_count(session, user.id) == recovery.CODE_COUNT


def test_forgot_password_requires_csrf(client, make_user):
    uid, _ = make_user("csrf2@example.com")
    with SessionLocal() as session:
        codes = recovery.issue(session, uid)

    resp = client.post(
        "/forgot-password",
        data={
            "email": "csrf2@example.com",
            "recovery_code": codes[0],
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
    )
    assert resp.status_code == 400
    with SessionLocal() as session:
        assert verify_password(session.get(User, uid).password_hash, PASSWORD)


# ---------------------------------------------------------------------------
# 設定頁重新產生
# ---------------------------------------------------------------------------


def test_regenerate_requires_current_password(client, make_user):
    uid, _ = make_user("regen@example.com")
    with SessionLocal() as session:
        old = recovery.issue(session, uid)
    login(client, "regen@example.com")

    resp = client.post(
        "/settings/recovery-codes",
        data={"current_password": "wrong-password", "csrf_token": csrf_from(client, "/settings/")},
        follow_redirects=True,
    )
    assert "目前密碼不正確" in resp.get_data(as_text=True)
    with SessionLocal() as session:
        assert recovery.consume(session, uid, old[0]) is True, "舊碼不該被作廢"


def test_regenerate_returns_a_fresh_batch(client, make_user):
    uid, _ = make_user("regen2@example.com")
    with SessionLocal() as session:
        old = recovery.issue(session, uid)
    login(client, "regen2@example.com")

    resp = client.post(
        "/settings/recovery-codes",
        data={"current_password": PASSWORD, "csrf_token": csrf_from(client, "/settings/")},
    )
    new = _codes_from(resp.get_data(as_text=True))
    assert len(new) == recovery.CODE_COUNT
    assert not set(new) & set(old)
    with SessionLocal() as session:
        assert recovery.consume(session, uid, old[0]) is False, "舊碼應已作廢"


def test_regenerate_requires_login(client):
    resp = client.post(
        "/settings/recovery-codes",
        data={"current_password": PASSWORD, "csrf_token": csrf_from(client, "/login")},
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_settings_page_title_is_not_polluted(client, make_user):
    """回歸測試：模板的 title 區塊不該混進版面內容。

    踩過兩次同一個坑——用 str.replace("{% endblock %}", ...) 插入區塊時
    沒有限制次數，而模板有兩個 endblock（title 與 content），結果整段
    HTML 被插進 <title> 裡，分頁標題變成一長串標籤。
    """
    make_user("title@example.com")
    login(client, "title@example.com")
    html = client.get("/settings/").get_data(as_text=True)

    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()
    assert title == "設定 — orgtd", f"title 被污染了：{title[:80]!r}"
    assert html.count("<h2>救援碼</h2>") == 1, "救援碼區塊應只出現一次"


# ---------------------------------------------------------------------------
# 併發、停用帳號、跨租戶、正規化
# ---------------------------------------------------------------------------


def test_concurrent_use_of_one_code_only_succeeds_once(make_user):
    """回歸測試：單次使用在併發下也必須成立。

    先查後寫的版本守不住——兩個請求的 SELECT 都會在對方 commit 之前讀到
    used_at IS NULL，雙方都判定成功。實測 8 條併發打同一組碼，每一輪都有
    6 到 8 條同時「消耗成功」。改成把條件放進 UPDATE 本身之後，資料庫的
    列鎖保證只有一條贏。
    """
    import threading

    uid, _ = make_user("concurrent@example.com")
    with SessionLocal() as session:
        codes = recovery.issue(session, uid)

    workers = 8
    results = []
    lock = threading.Lock()
    # 帶 timeout：某條執行緒若意外拋例外，其餘不該永遠卡在這裡
    # 讓整個測試掛住而不是乾淨地失敗。
    barrier = threading.Barrier(workers, timeout=10)
    sessions = [SessionLocal() for _ in range(workers)]
    for sess in sessions:  # 先暖開連線，讓 barrier 之後只剩查詢本身
        sess.execute(sa_text("SELECT 1"))

    def attempt(sess):
        barrier.wait()
        ok = recovery.consume(sess, uid, codes[0])
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=attempt, args=(s,)) for s in sessions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for sess in sessions:
        sess.close()

    assert sum(results) == 1, f"{sum(results)} 條同時消耗掉同一組碼"
    with SessionLocal() as session:
        assert recovery.unused_count(session, uid) == recovery.CODE_COUNT - 1


def test_deactivated_account_cannot_reset_password(client, make_user):
    """停用的帳號不該能用救援碼把自己救回來。"""
    uid, _ = make_user("disabled@example.com", active=False)
    with SessionLocal() as session:
        codes = recovery.issue(session, uid)

    resp = _reset(client, "disabled@example.com", codes[0])
    assert "救援碼不正確" in resp.get_data(as_text=True)
    with SessionLocal() as session:
        assert verify_password(session.get(User, uid).password_hash, PASSWORD)
        assert recovery.unused_count(session, uid) == recovery.CODE_COUNT, "碼不該被消耗"


def test_reissue_does_not_touch_other_users_codes(make_user):
    """A 重新產生救援碼，不該影響 B 的。

    issue() 的刪除若漏掉 user_id 過濾就會清掉全表，而只驗證「同一使用者
    舊碼失效」的測試抓不到這種錯。
    """
    a_id, _ = make_user("aa@example.com")
    b_id, _ = make_user("bb@example.com")
    with SessionLocal() as session:
        recovery.issue(session, a_id)
        b_codes = recovery.issue(session, b_id)

    with SessionLocal() as session:
        recovery.issue(session, a_id)  # A 重新產生
        assert recovery.unused_count(session, b_id) == recovery.CODE_COUNT
        assert recovery.consume(session, b_id, b_codes[0]) is True, "B 的碼不該被牽連"


def test_reset_finds_the_account_regardless_of_email_case(client):
    """忘記密碼的帳號查找必須跟登入用同一套正規化基準。"""
    codes = _codes_from(_register(client, email="MiXeD@Example.COM").get_data(as_text=True))
    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    resp = _reset(client, "  mIxEd@EXAMPLE.com  ", codes[0])
    assert "密碼已重設" in resp.get_data(as_text=True)

    login(client, "mixed@example.com", NEW_PASSWORD)
    assert client.get("/").status_code == 200


def test_recovery_page_is_not_cacheable(client):
    """明碼頁是全站唯一含長期有效機密的回應，不能留在瀏覽器快取裡。"""
    resp = _register(client)
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_backslash_in_display_name_does_not_break_the_page(client):
    """顯示名稱含反斜線時，下載用的內嵌資料不能把 script 打斷。

    早先是在 JS 字串裡直接插 display_name。Jinja 的自動跳脫是給 HTML 用
    的，不處理反斜線——名字以 \\ 結尾就會跳脫掉收尾引號，整段 script
    SyntaxError，下載與複製按鈕靜默失效。
    """
    resp = client.post(
        "/register",
        data={
            "email": "slash@example.com",
            "display_name": "Jerry\\",
            "password": "a-long-enough-passphrase",
            "password_confirm": "a-long-enough-passphrase",
            "csrf_token": csrf_from(client, "/register"),
        },
    )
    html = resp.get_data(as_text=True)
    payload = re.search(
        r'<script id="download-payload" type="application/json">(.*?)</script>',
        html, re.S,
    )
    assert payload, "找不到下載內容的載體"
    # 必須是合法 JSON——若跳脫壞掉，這裡會直接解析失敗
    text = json.loads(payload.group(1))
    assert "Jerry\\" in text
    assert len(_codes_from(html)) == recovery.CODE_COUNT


def test_verification_runs_even_for_unknown_accounts(client, make_user, monkeypatch):
    """回歸測試：查無此帳號時也必須跑一次驗證。

    寫成 `user is None or not recovery.consume(...)` 的話，Python 的短路會讓
    「查無此帳號」跳過雜湊與資料庫查詢，明顯比「帳號存在但碼錯」快——訊息
    藏好了，卻從耗時洩漏出帳號是否存在。

    直接量測時間會做出不穩定的測試，所以改成斷言「consume 有沒有被呼叫」
    這個結構性質，效果一樣而且不會偶發失敗。
    """
    import views.auth

    calls = []
    real = recovery.consume
    monkeypatch.setattr(
        views.auth.recovery, "consume",
        lambda session, uid, code: calls.append(uid) or real(session, uid, code),
    )

    _reset(client, "definitely-not-registered@example.com", "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA")
    assert calls, "查無此帳號時也該跑一次驗證，否則耗時會洩漏帳號是否存在"
    assert calls[0] < 0, "應該用不可能存在的哨兵 id，而不是跳過查詢"


def test_oversized_input_is_rejected_before_scanning(make_user, monkeypatch):
    """超長輸入要在逐字元掃描之前就擋掉。

    /forgot-password 是未登入端點，不該讓人用一個超大的欄位逼伺服器
    掃完整段輸入才發現長度不對。
    """
    uid, _ = make_user("oversized@example.com")
    with SessionLocal() as session:
        recovery.issue(session, uid)

    scanned = []
    real = recovery.canonical
    monkeypatch.setattr(
        recovery, "canonical", lambda raw: scanned.append(len(raw)) or real(raw)
    )

    with SessionLocal() as session:
        assert recovery.consume(session, uid, "A" * 100_000) is False
    assert scanned == [], "超長輸入不該進到 canonical() 的逐字元掃描"


def test_settings_password_change_uses_the_shared_minimum(client, make_user):
    """改密碼的長度下限必須跟註冊、重設共用同一個常數。

    前端的 minlength 與提示文字都吃 MIN_PASSWORD_LENGTH，後端若各自硬編碼
    一個數字，調高下限時前端會承諾一件後端沒在擋的事。
    """
    from views.auth import MIN_PASSWORD_LENGTH

    make_user("minlen@example.com")
    login(client, "minlen@example.com")

    just_short = "x" * (MIN_PASSWORD_LENGTH - 1)
    resp = client.post(
        "/settings/password",
        data={
            "current_password": PASSWORD,
            "new_password": just_short,
            "new_password_confirm": just_short,
            "csrf_token": csrf_from(client, "/settings/"),
        },
        follow_redirects=True,
    )
    assert f"至少 {MIN_PASSWORD_LENGTH} 個字元" in resp.get_data(as_text=True)

    just_long = "x" * MIN_PASSWORD_LENGTH
    resp = client.post(
        "/settings/password",
        data={
            "current_password": PASSWORD,
            "new_password": just_long,
            "new_password_confirm": just_long,
            "csrf_token": csrf_from(client, "/settings/"),
        },
        follow_redirects=True,
    )
    assert "密碼已更新" in resp.get_data(as_text=True)
