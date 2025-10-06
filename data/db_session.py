"""
data/db_session.py
مدیریت اتصال به پایگاه‌داده PostgreSQL و ایجاد Session
"""

import os
from urllib.parse import quote_plus
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from config_manager import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME
from models import Base

class DBSessionManager:
    """
    مدیریت ارتباط با PostgreSQL با پشتیبانی از لاگین پویا.
    """

    def __init__(self, db_user: str | None = None, db_password: str | None = None):
        # تعیین نام کاربری و رمز عبور
        user = (db_user or os.getenv("APP_DB_USER") or DB_USER or "").strip()
        pwd  = (db_password or os.getenv("APP_DB_PASSWORD") or DB_PASSWORD or "").strip()

        # ساخت URL امن
        user_enc = quote_plus(user)
        pwd_enc  = quote_plus(pwd)

        db_url = f"postgresql+psycopg2://{user_enc}:{pwd_enc}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

        # ایجاد engine
        self.engine = create_engine(
            db_url,
            echo=False,
            pool_size=10,
            max_overflow=20
        )

        # ساخت جدول‌ها اگر وجود ندارند
        Base.metadata.create_all(self.engine)

        # ایجاد کارخانه Session
        self.Session = sessionmaker(bind=self.engine)

    @staticmethod
    def test_connection(db_user: str, db_password: str) -> tuple[bool, str]:
        """تست اتصال دیتابیس"""
        try:
            user_enc = quote_plus(db_user.strip())
            pwd_enc  = quote_plus(db_password.strip())
            db_url = f"postgresql+psycopg2://{user_enc}:{pwd_enc}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            test_engine = create_engine(db_url)
            with test_engine.connect() as conn:
                conn.execute(func.now())
            return True, "OK"
        except OperationalError as e:
            return False, f"اتصال ناموفق: {e}"
        except Exception as e:
            return False, f"خطا: {e}"

    def get_session(self):
        """ایجاد یک Session جدید"""
        return self.Session()

# حذف این خط!
# db_session_manager = DBSessionManager()
