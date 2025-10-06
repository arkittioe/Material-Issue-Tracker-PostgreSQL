# file: data/iso_service.py
import os
import glob
import logging
from datetime import datetime
from typing import Optional, Callable, List

from sqlalchemy.orm import Session

from data.db_session import DBSessionManager
from models import IsoFileIndex


class ISOService:
    """
    سرویس مدیریت ایندکس فایل‌های ISO
    بر اساس منطق نسخه مونولیت data_manager.py (خطوط 1657 تا 1799)
    """

    def __init__(
        self,
        session_getter: Callable[[], Session] = DBSessionManager.get_session
    ):
        self._session_getter = session_getter

    # ------------------------------------------------------------------
    # find_iso_files
    # ------------------------------------------------------------------
    def find_iso_files(self, directory_path: str, pattern: str = "*.pdf") -> List[str]:
        """
        جستجو برای یافتن مسیر کامل فایل‌های ISO (به طور پیش‌فرض PDF) در یک مسیر مشخص.
        """
        try:
            search_path = os.path.join(directory_path, pattern)
            return glob.glob(search_path)
        except Exception as e:
            logging.error(f"خطا در find_iso_files: {e}")
            return []

    # ------------------------------------------------------------------
    # upsert_iso_index_entry
    # ------------------------------------------------------------------
    def upsert_iso_index_entry(self, file_path: str, session: Optional[Session] = None) -> None:
        """
        اضافه یا به‌روزرسانی رکورد اندیس ISO در دیتابیس.
        اگر رکورد موجود با همان مسیر باشد، اطلاعات آن به‌روزرسانی می‌شود.
        """
        own_session = False
        if session is None:
            session = self._session_getter()
            own_session = True

        try:
            filename = os.path.basename(file_path)
            prefix_key = self._extract_prefix_key(filename)
            last_modified = datetime.fromtimestamp(os.path.getmtime(file_path))

            existing = (
                session.query(IsoFileIndex)
                .filter(IsoFileIndex.file_path == file_path)
                .first()
            )

            if existing:
                existing.prefix_key = prefix_key
                existing.last_modified = last_modified
            else:
                new_entry = IsoFileIndex(
                    file_path=file_path,
                    prefix_key=prefix_key,
                    last_modified=last_modified
                )
                session.add(new_entry)

            if own_session:
                session.commit()

        except Exception as e:
            if own_session:
                session.rollback()
            logging.error(f"خطا در upsert_iso_index_entry: {e}")
            raise
        finally:
            if own_session:
                session.close()

    # ------------------------------------------------------------------
    # remove_iso_index_entry
    # ------------------------------------------------------------------
    def remove_iso_index_entry(self, prefix_key: str, session: Optional[Session] = None) -> int:
        """
        حذف رکورد ISO از دیتابیس بر اساس prefix_key
        :return: تعداد رکوردهای حذف شده
        """
        own_session = False
        if session is None:
            session = self._session_getter()
            own_session = True

        try:
            deleted_count = (
                session.query(IsoFileIndex)
                .filter(IsoFileIndex.prefix_key == prefix_key)
                .delete(synchronize_session=False)
            )
            if own_session:
                session.commit()
            return deleted_count

        except Exception as e:
            if own_session:
                session.rollback()
            logging.error(f"خطا در remove_iso_index_entry: {e}")
            raise
        finally:
            if own_session:
                session.close()

    # ------------------------------------------------------------------
    # rebuild_iso_index_from_scratch
    # ------------------------------------------------------------------
    def rebuild_iso_index_from_scratch(self, base_directory: str, pattern: str = "*.pdf") -> None:
        """
        بازسازی کامل اندیس ISO:
        1. پاک کردن همه رکوردها
        2. پیدا کردن تمام فایل‌ها در مسیر
        3. افزودن هر مسیر فایل به دیتابیس
        """
        session = self._session_getter()
        try:
            # مرحله 1: حذف همه رکوردهای موجود
            session.query(IsoFileIndex).delete(synchronize_session=False)
            session.commit()

            # مرحله 2: جستجوی فایل‌ها
            files = self.find_iso_files(base_directory, pattern=pattern)

            # مرحله 3: درج مجدد
            for f in files:
                self.upsert_iso_index_entry(f, session=session)

            session.commit()

        except Exception as e:
            session.rollback()
            logging.error(f"خطا در rebuild_iso_index_from_scratch: {e}")
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # _extract_prefix_key
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_prefix_key(filename: str) -> str:
        """
        استخراج کلید پیشوند از نام فایل.
        منطق این تابع باید با نسخه مونولیت یکسان باشد.
        """
        if not filename:
            return ""
        name_no_ext = os.path.splitext(filename)[0]
        # حذف فاصله‌ها و کاراکترهای خاص، تبدیل به حروف بزرگ
        cleaned = "".join(ch for ch in name_no_ext.upper() if ch.isalnum())
        # پیشوند پیش‌فرض 6 کاراکتر
        return cleaned[:6]
