"""註冊、登入、登出與存取控制。"""

import unicodedata

from sqlalchemy import select

from conftest import PASSWORD, csrf_from, login
from db import SessionLocal
from models import User


def test_anonymous_is_redirected_to_login(client):
    for path in ["/", "/agenda", "/projects/", "/dashboard", "/settings/", "/notes/"]:
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "/login" in resp.headers["Location"], path


def test_register_then_access(client):
    token = csrf_from(client, "/register")
    resp = client.post(
        "/register",
        data={
            "email": "new@example.com",
            "display_name": "新使用者",
            "password": "a-long-enough-passphrase",
            "password_confirm": "a-long-enough-passphrase",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 302
    assert client.get("/").status_code == 200


def test_register_rejects_short_password(client):
    token = csrf_from(client, "/register")
    resp = client.post(
        "/register",
        data={
            "email": "short@example.com",
            "display_name": "x",
            "password": "short",
            "password_confirm": "short",
            "csrf_token": token,
        },
    )
    assert "至少 12 個字元" in resp.get_data(as_text=True)


def test_register_rejects_malformed_email(client):
    """註冊端的 email 格式檢查必須真的生效。

    這些值都含有 @，會通過早期那版「只檢查有沒有小老鼠」的邏輯，
    所以少了這支測試的話，把 normalize_email() 換回舊寫法不會有任何測試失敗。
    """
    for bad in ["a b@example.com", "a@@b.com", "@example.com", "a@.com", "沒有小老鼠"]:
        resp = client.post(
            "/register",
            data={
                "email": bad,
                "display_name": "x",
                "password": "a-long-enough-passphrase",
                "password_confirm": "a-long-enough-passphrase",
                "csrf_token": csrf_from(client, "/register"),
            },
        )
        assert "格式不正確" in resp.get_data(as_text=True), bad

    with SessionLocal() as session:
        assert session.query(User).count() == 0, "不該有任何帳號被建立"


def test_register_normalises_email_case(client):
    """大小寫混雜的 email 註冊後，要能用任何大小寫組合登入。"""
    client.post(
        "/register",
        data={
            "email": "  MiXeD@Example.COM  ",
            "display_name": "x",
            "password": "a-long-enough-passphrase",
            "password_confirm": "a-long-enough-passphrase",
            "csrf_token": csrf_from(client, "/register"),
        },
    )
    with SessionLocal() as session:
        assert session.scalar(select(User.email)) == "mixed@example.com"

    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})
    login(client, "MIXED@EXAMPLE.COM", "a-long-enough-passphrase")
    assert client.get("/").status_code == 200


def test_register_rejects_duplicate_email(client, make_user):
    make_user("dup@example.com")
    token = csrf_from(client, "/register")
    resp = client.post(
        "/register",
        data={
            "email": "dup@example.com",
            "display_name": "x",
            "password": "a-long-enough-passphrase",
            "password_confirm": "a-long-enough-passphrase",
            "csrf_token": token,
        },
    )
    assert "已經註冊過" in resp.get_data(as_text=True)


def test_login_success_and_wrong_password(client, make_user):
    make_user("a@example.com")
    assert login(client, "a@example.com").status_code == 302
    assert client.get("/").status_code == 200

    client.get("/logout")  # GET 不是登出路由，狀態應該不變
    assert client.get("/").status_code == 200

    token = csrf_from(client, "/")
    client.post("/logout", data={"csrf_token": token})
    assert client.get("/").status_code == 302


def test_wrong_password_message_does_not_leak_account_existence(client, make_user):
    make_user("real@example.com")
    r1 = login(client, "real@example.com", "wrong-password-here")
    r2 = login(client, "nobody@example.com", "wrong-password-here")
    assert "email 或密碼不正確" in r1.get_data(as_text=True)
    assert "email 或密碼不正確" in r2.get_data(as_text=True)


def test_account_without_password_cannot_log_in(client, make_user):
    """password_hash 為 NULL 的帳號不得因為「空密碼比對成功」而登入。

    這不是假設性的狀況：舊單人資料遷移過來的站長帳號就是這個狀態，
    要先跑 manage.py set-password 才能登入。
    """
    make_user("nopw@example.com")
    with SessionLocal() as session:
        u = session.scalar(select(User).where(User.email == "nopw@example.com"))
        u.password_hash = None
        session.commit()

    for attempt in ["", PASSWORD, "anything"]:
        login(client, "nopw@example.com", attempt)
        assert client.get("/").status_code == 302


def test_legacy_account_with_nonstandard_email_can_still_log_in(client):
    """格式不符現行規則的舊帳號仍然要能登入。

    migration 產生的站長帳號、或早期沒有格式檢查時註冊的帳號，email 未必
    通過現在的 normalize_email()。登入端因此在正規化失敗時退回單純的去空白
    轉小寫——少了那條退路，這些人會被自己的資料永久鎖在門外。

    這支測試直接寫入資料庫，繞過 /register，因為現行註冊流程根本不允許
    建立這種帳號。
    """
    from emails import normalize_email
    from security import hash_password

    legacy_email = "owner@localhost"  # 沒有點的網域，通不過現行格式檢查
    assert normalize_email(legacy_email)[1] is not None, "測試前提：這個 email 應被判為格式不合"

    password = "a-long-enough-passphrase"
    with SessionLocal() as session:
        session.add(
            User(
                email=legacy_email,
                display_name="舊帳號",
                password_hash=hash_password(password),
            )
        )
        session.commit()

    login(client, legacy_email, password)
    assert client.get("/").status_code == 200, "舊帳號應該登得進去"


def test_lookup_key_adds_no_processing_beyond_normalize_email(client):
    """格式合法時，lookup_key 必須原封不動回傳 normalize_email 的結果。

    這支測試釘住的是不變量本身，而不是某一種正規化規則：不論
    normalize_email 未來怎麼改（NFC、IDNA/UTS-46 映射、或別的），
    lookup_key 都不該在它之外再加工。

    全形網域是實際踩過的案例——validate_email 會把 ｅｘａｍｐｌｅ 映射成
    example，而只做 NFC 的實作不會，兩者算出不同的鍵。

    注意這裡驗的是函式層級的不變量；「註冊真的存了這個值、登入真的查得到」
    由 test_register_and_login_survive_idna_domain_mapping 端對端守住。
    """
    from emails import lookup_key, normalize_email

    for raw in [
        "Plain@Example.COM",
        unicodedata.normalize("NFD", "josé@example.com"),
        "fw@ｅｘａｍｐｌｅ.COM",
        "  padded@example.com  ",
    ]:
        stored, error = normalize_email(raw)
        assert error is None, f"{raw!r} 應為合法格式"
        assert lookup_key(raw) == stored, f"{raw!r} 的查詢鍵與寫入值不一致"


def test_lookup_key_falls_back_without_extra_normalisation(client):
    """格式不合規則的舊帳號，查詢鍵只做去空白轉小寫，不額外做 NFC。

    那些值當初就是以原始位元存進資料庫的，多做一次正規化反而對不上。
    """
    from emails import lookup_key

    decomposed = unicodedata.normalize("NFD", "café@localhost")
    assert lookup_key(decomposed) == decomposed.strip().lower()
    assert lookup_key("  Owner@LocalHost ") == "owner@localhost"


def test_register_and_login_survive_idna_domain_mapping(client):
    """端對端：全形網域註冊之後，用原始輸入或等價的半形都要登得進去。

    這支測試補的是最關鍵的死角。既有的登入／註冊測試全用純 ASCII，而純
    ASCII 下 lookup_key() 跟天真的 .strip().lower() 行為完全一樣——也就是
    說，把 login() 改回自己拼一套正規化，整個測試套件不會有任何一支變紅，
    而那正是這輪工作要根除的漂移。

    全形網域能區分兩者：validate_email 會做 IDNA/UTS-46 映射把
    ｅｘａｍｐｌｅ 轉成 example，只做 NFC 的實作不會。
    """
    fullwidth = "fw@ｅｘａｍｐｌｅ.COM"
    ascii_form = "fw@example.com"
    password = "a-long-enough-passphrase"

    client.post(
        "/register",
        data={
            "email": fullwidth,
            "display_name": "全形網域",
            "password": password,
            "password_confirm": password,
            "csrf_token": csrf_from(client, "/register"),
        },
    )
    with SessionLocal() as session:
        assert session.scalar(select(User.email)) == ascii_form, "註冊應存成映射後的形式"

    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    # 使用者原本打的那串全形字，必須還能登回去
    login(client, fullwidth, password)
    assert client.get("/").status_code == 200, "用註冊時的原始輸入應該登得進去"

    client.post("/logout", data={"csrf_token": csrf_from(client, "/")})

    # 等價的半形寫法同樣要通
    login(client, ascii_form, password)
    assert client.get("/").status_code == 200, "等價的半形寫法也應該登得進去"
