"""變更登入用 email。

email 是帳號的主要識別，所以這裡的重點不只是「能不能改成功」，
更是「不該成功的時候有沒有確實擋下來」。
"""

import unicodedata

from sqlalchemy import select

from conftest import PASSWORD, csrf_from, login
from db import SessionLocal
from models import User
from emails import EMAIL_MAX_LENGTH, normalize_email


def _email_of(user_id):
    with SessionLocal() as session:
        return session.get(User, user_id).email


def _post_email(client, new_email, password=PASSWORD):
    return client.post(
        "/settings/email",
        data={
            "new_email": new_email,
            "current_password": password,
            "csrf_token": csrf_from(client, "/settings/"),
        },
        follow_redirects=True,
    )


def test_change_email_succeeds(client, make_user):
    uid, _ = make_user("before@example.com")
    login(client, "before@example.com")

    resp = _post_email(client, "after@example.com")
    assert "email 已更新" in resp.get_data(as_text=True)
    assert _email_of(uid) == "after@example.com"


def test_can_log_in_with_new_email_and_not_the_old(client, make_user):
    make_user("old@example.com")
    login(client, "old@example.com")
    _post_email(client, "new@example.com")

    token = csrf_from(client, "/")
    client.post("/logout", data={"csrf_token": token})

    login(client, "old@example.com")
    assert client.get("/").status_code == 302, "舊 email 不該還能登入"

    login(client, "new@example.com")
    assert client.get("/").status_code == 200, "新 email 應該能登入"


def test_wrong_password_is_rejected(client, make_user):
    uid, _ = make_user("keep@example.com")
    login(client, "keep@example.com")

    resp = _post_email(client, "hijack@example.com", password="not-the-password")
    assert "目前密碼不正確" in resp.get_data(as_text=True)
    assert _email_of(uid) == "keep@example.com"


def test_cannot_take_another_users_email(client, make_user):
    uid, _ = make_user("me@example.com")
    make_user("taken@example.com")
    login(client, "me@example.com")

    resp = _post_email(client, "taken@example.com")
    assert "已經被其他帳號使用" in resp.get_data(as_text=True)
    assert _email_of(uid) == "me@example.com"


def test_rejects_invalid_format(client, make_user):
    uid, _ = make_user("valid@example.com")
    login(client, "valid@example.com")

    for bad in ["沒有小老鼠", "a@@b.com", "@example.com", "a b@example.com", ""]:
        resp = _post_email(client, bad)
        body = resp.get_data(as_text=True)
        assert ("格式不正確" in body) or ("請填寫 email" in body), bad
        assert _email_of(uid) == "valid@example.com", bad


def test_email_is_stored_lowercase(client, make_user):
    """存進去若保留大小寫，之後用小寫查就找不到人，等於自己把自己鎖在外面。"""
    uid, _ = make_user("lower@example.com")
    login(client, "lower@example.com")

    _post_email(client, "  MiXeD.Case@Example.COM  ")
    assert _email_of(uid) == "mixed.case@example.com"

    token = csrf_from(client, "/")
    client.post("/logout", data={"csrf_token": token})
    login(client, "MIXED.CASE@EXAMPLE.COM")
    assert client.get("/").status_code == 200


def test_changing_to_the_same_email_is_a_noop(client, make_user):
    uid, _ = make_user("same@example.com")
    login(client, "same@example.com")

    resp = _post_email(client, "same@example.com")
    assert "跟目前這個一樣" in resp.get_data(as_text=True)
    assert _email_of(uid) == "same@example.com"


def test_requires_login(client):
    """未登入必須被導去登入頁。

    這裡一定要帶有效的 CSRF token：CSRFProtect 掛在 before_request，
    執行順序早於 @login_required，不帶 token 的話永遠先拿到 400，
    根本走不到登入檢查——那樣就只是把 CSRF 測試再測一遍而已。
    """
    token = csrf_from(client, "/login")
    resp = client.post(
        "/settings/email",
        data={"new_email": "x@example.com", "csrf_token": token},
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_requires_csrf_token(client, make_user):
    uid, _ = make_user("csrf@example.com")
    login(client, "csrf@example.com")

    resp = client.post(
        "/settings/email",
        data={"new_email": "nope@example.com", "current_password": PASSWORD},
    )
    assert resp.status_code == 400
    assert _email_of(uid) == "csrf@example.com"


def test_account_without_password_can_still_change_email(client, make_user):
    """尚未設密碼的帳號不該被密碼關卡鎖在門外——它本來就沒有可驗證的憑證。

    先正常登入再把 password_hash 清成 NULL，模擬「登入後憑證被移除」的
    狀態。直接塞 session 繞過登入是行不通的：session_protection="strong"
    會因為缺少身分指紋而立刻登出。
    """
    uid, _ = make_user("nopw2@example.com")
    login(client, "nopw2@example.com")

    with SessionLocal() as session:
        session.get(User, uid).password_hash = None
        session.commit()

    _post_email(client, "haspw@example.com", password="")
    assert _email_of(uid) == "haspw@example.com"


def test_normalize_email_helper():
    assert normalize_email("  A@B.CO ") == ("a@b.co", None)
    assert normalize_email("")[0] is None
    # 沒有點的網域（jerry@local）在標準規則下不合法
    assert normalize_email("jerry@local")[0] is None


def test_length_limit_is_our_own_check_not_the_library_s():
    """超長 email 必須由我們自己的長度檢查擋下，並回報自己的訊息。

    只斷言「回傳 None」是不夠的：email_validator 對超長字串本來就會拋錯，
    所以那種斷言即使把 security.py 的長度檢查整段刪掉也照樣通過，
    等於這條防線沒有被任何測試證明過。改成比對訊息才真的釘得住。
    """
    value, error = normalize_email("x" * 300 + "@a.com")
    assert value is None
    assert error == f"email 不能超過 {EMAIL_MAX_LENGTH} 個字元。"


def test_unicode_is_normalised_consistently_between_register_and_login(client):
    """回歸測試：分解形式（NFD）的 email 註冊後必須登得回去。

    normalize_email() 會做 Unicode NFC 正規化，而登入端若只做 .lower()，
    用組合重音（e + U+0301）註冊的人會存進 NFC、登入時拿原始位元查，
    永遠對不上——密碼完全正確也被拒，而這個系統沒有忘記密碼流程可以自救。
    """
    composed = unicodedata.normalize("NFC", "josé@example.com")
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed, "測試前提：兩種形式的位元必須不同"

    password = "a-long-enough-passphrase"
    client.post(
        "/register",
        data={
            "email": decomposed,
            "display_name": "NFD 測試",
            "password": password,
            "password_confirm": password,
            "csrf_token": csrf_from(client, "/register"),
        },
    )
    with SessionLocal() as session:
        stored = session.scalar(select(User.email))
    assert stored == composed, "註冊應存成 NFC 形式"

    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    # 使用者再次輸入「一模一樣的位元」——也就是分解形式——必須登得進去
    client.post(
        "/login",
        data={
            "email": decomposed,
            "password": password,
            "csrf_token": csrf_from(client, "/login"),
        },
    )
    assert client.get("/").status_code == 200, "用註冊時的原始輸入應該登得回去"


def test_race_on_unique_email_is_handled_gracefully(client, make_user, monkeypatch):
    """應用層檢查與 commit 之間的空窗，要由資料庫約束擋下且不能變成 500。

    真實的競態難以在測試裡穩定重現，所以直接讓應用層的檢查回報「沒被使用」，
    模擬「查詢時還沒人佔用、commit 時已經被搶走」的那一瞬間。
    """
    uid, _ = make_user("racer@example.com")
    make_user("contested@example.com")
    login(client, "racer@example.com")

    import views.settings

    monkeypatch.setattr(views.settings, "_email_taken", lambda *a, **kw: False)

    resp = _post_email(client, "contested@example.com")

    assert resp.status_code == 200, "應正常回應，不該是 500"
    assert "已經被其他帳號使用" in resp.get_data(as_text=True)
    assert _email_of(uid) == "racer@example.com"


def test_email_taken_excludes_the_caller(make_user):
    """_email_taken 不該把「使用者自己」算成佔用者。

    呼叫端目前會先用 `new_email == user.email` 短路擋掉這個情境，所以這條
    排除條件走不到——直接對函式做單元測試，才能證明它真的有作用，而不是
    一段沒人驗證過的防禦性程式碼。
    """
    from views.settings import _email_taken

    mine, _ = make_user("mine@example.com")
    theirs, _ = make_user("theirs@example.com")

    with SessionLocal() as session:
        assert _email_taken(session, "theirs@example.com", mine) is True
        assert _email_taken(session, "mine@example.com", mine) is False
        assert _email_taken(session, "mine@example.com", theirs) is True
        assert _email_taken(session, "nobody@example.com", mine) is False
