"""資料庫連線設定：engine / session / Base。

本機單人使用，連線字串優先讀環境變數 ORGTD_DATABASE_URL，
沒設定就用 Homebrew PostgreSQL 本機預設（目前使用者即 superuser，免密碼）。
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


DATABASE_URL = os.environ.get(
    "ORGTD_DATABASE_URL",
    "postgresql+psycopg://jerrychen@localhost:5432/orgtd",
)

engine = create_engine(DATABASE_URL, echo=False, future=True)

# expire_on_commit=False：commit 後仍可讀取物件屬性，
# 伺服器渲染的 view 函式常在 commit 後還要把物件丟給模板。
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
