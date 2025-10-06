# file: data/activity_service.py
"""
سرویس ثبت و دریافت لاگ‌های فعالیت کاربران و سیستم
مطابق method_map و کد اصلی data_manager.py
"""

import logging
from datetime import datetime
from typing import Optional, List
"""
data/activity_service.py
سرویس مدیریت لاگ فعالیت‌ها
"""

from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from models import ActivityLog


# حذف این خط:
# from data.db_session import db_session_manager

class ActivityService:
    """سرویس ثبت و مدیریت لاگ فعالیت‌ها"""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def log_activity(self, user: str, action: str, details: str = "", session: Optional[Session] = None):
        """ثبت لاگ در جدول ActivityLog"""
        own_session = False
        if session is None:
            session = self.session_factory()
            own_session = True

        try:
            log = ActivityLog(
                user=user,
                action=action,
                details=details,
                timestamp=datetime.now()
            )
            session.add(log)
            if own_session:
                session.commit()
        except Exception as e:
            if own_session:
                session.rollback()
            print(f"⚠️ خطا در ثبت لاگ: {e}")
        finally:
            if own_session:
                session.close()

    # ===================================================
    # ۲. واکشی آخرین لاگ‌ها
    # ===================================================
    def get_activity_logs(self, limit: int = 100) -> List[ActivityLog]:
        """
        آخرین N رکورد از جدول ActivityLog را برمی‌گرداند.

        :param limit: تعداد رکوردها (پیش‌فرض: 100)
        """
        session = self._session_getter()
        try:
            return (
                session.query(ActivityLog)
                .order_by(ActivityLog.timestamp.desc())
                .limit(limit)
                .all()
            )
        except Exception as e:
            logging.error(f"Error fetching activity logs: {e}")
            return []
        finally:
            session.close()
