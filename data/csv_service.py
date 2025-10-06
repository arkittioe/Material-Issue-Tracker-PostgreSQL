# file: data/csv_service.py
"""
سرویس مدیریت CSV:
    - بارگذاری و به‌روزرسانی MTO از فایل CSV
    - پردازش همزمان چند فایل CSV برای MTO و Spool
    - توابع کمکی برای نرمال‌سازی داده‌ها و کلید خطوط
"""

import os
import re
import logging
from datetime import datetime
from typing import List, Tuple, Dict, Any, Callable, Optional

import pandas as pd
from sqlalchemy.orm import Session

from data.db_session import DBSessionManager
from models import Project, MTOItem, MTOConsumption, MTOProgress


class CSVService:
    def __init__(
        self,
        activity_logger: Callable[[str, str, str, Optional[Session]], None],
        project_getter: Callable[[Session, str], Project],
        spool_replacer: Callable[[str, str], Tuple[bool, str]],
        session_getter: Callable[[], Session] = DBSessionManager.get_session,
    ):
        """
        :param activity_logger: تابع ثبت فعالیت (مثل ActivityService.log_activity)
        :param project_getter: تابع دریافت یا ساخت پروژه (مثل ProjectService.get_or_create_project)
        :param spool_replacer: تابع جایگزینی کامل داده‌های اسپول (مثل SpoolService.replace_all_spool_data)
        :param session_getter: وظیفه بازگردانی یک Session دیتابیس
        """
        self.log_activity = activity_logger
        self.get_or_create_project = project_getter
        self.replace_all_spool_data = spool_replacer
        self._session_getter = session_getter

    # --------------------------------------------------
    # متدهای اصلی
    # --------------------------------------------------

    def update_project_mto_from_csv(self, project_name: str, mto_file_path: str) -> Tuple[bool, str]:
        """
        به‌روزرسانی داده‌های MTO برای پروژه مشخص شده از روی فایل CSV
        """
        REQUIRED_DB_COLS = {'line_no', 'description'}
        MTO_COLUMN_MAP = {
            'UNIT': 'unit', 'LINENO': 'line_no', 'CLASS': 'item_class', 'TYPE': 'item_type',
            'DESCRIPTION': 'description', 'ITEMCODE': 'item_code', 'MAT': 'material_code',
            'P1BOREIN': 'p1_bore_in', 'P2BOREIN': 'p2_bore_in', 'P3BOREIN': 'p3_bore_in',
            'LENGTHM': 'length_m', 'QUANTITY': 'quantity', 'JOINT': 'joint', 'INCHDIA': 'inch_dia'
        }

        session = self._session_getter()
        try:
            with session.begin():
                self.log_activity("system", "MTO_UPDATE_START",
                                  f"شروع آپدیت MTO برای پروژه '{project_name}'.", session)

                project = self.get_or_create_project(session, project_name)
                project_id = project.id

                mto_df_raw = pd.read_csv(mto_file_path, dtype=str).fillna('')
                mto_df = self._normalize_and_rename_df(
                    mto_df_raw, MTO_COLUMN_MAP, REQUIRED_DB_COLS, os.path.basename(mto_file_path)
                )
                mto_df['project_id'] = project_id

                # اطمینان از نوع عددی برای ستون‌های کمی
                for col in ['p1_bore_in', 'p2_bore_in', 'p3_bore_in',
                            'length_m', 'quantity', 'joint', 'inch_dia']:
                    if col in mto_df.columns:
                        mto_df[col] = pd.to_numeric(mto_df[col], errors='coerce')

                # حذف داده‌های قدیمی پروژه
                mto_ids = session.query(MTOItem.id).filter(MTOItem.project_id == project_id).scalar_subquery()
                session.query(MTOConsumption).filter(
                    MTOConsumption.mto_item_id.in_(mto_ids)
                ).delete(synchronize_session=False)

                session.query(MTOProgress).filter(
                    MTOProgress.project_id == project_id
                ).delete(synchronize_session=False)

                session.query(MTOItem).filter(
                    MTOItem.project_id == project_id
                ).delete(synchronize_session=False)

                session.flush()

                # درج داده‌های جدید
                records = mto_df.to_dict(orient='records')
                if records:
                    session.bulk_insert_mappings(MTOItem, records)

            self.log_activity("system", "MTO_UPDATE_SUCCESS",
                              f"{len(mto_df)} آیتم MTO برای '{project_name}' آپدیت شد.")
            return True, f"✔ داده‌های MTO برای پروژه '{project_name}' با موفقیت به‌روزرسانی شدند."

        except (ValueError, KeyError, FileNotFoundError) as e:
            session.rollback()
            logging.error(f"شکست در آپدیت MTO برای {project_name}: {e}")
            return False, f"خطا در فایل MTO پروژه '{project_name}': {e}"
        except Exception as e:
            session.rollback()
            logging.error(f"Unexpected error during MTO update for {project_name}: {e}")
            return False, f"خطای غیرمنتظره در آپدیت MTO پروژه '{project_name}': {e}"
        finally:
            session.close()

    def process_selected_csv_files(self, file_paths: List[str]) -> Tuple[bool, str]:
        """
        پردازش چندین فایل CSV انتخاب شده و تفکیک آنها به MTO و Spool
        """
        try:
            mto_files: Dict[str, str] = {}
            spool_file = None
            spool_items_file = None

            for path in file_paths:
                fname = os.path.basename(path)
                if fname.upper().startswith("MTO-") and fname.upper().endswith(".CSV"):
                    project_name = fname.replace("MTO-", "").replace(".csv", "")
                    mto_files[project_name] = path
                elif fname.upper() == "SPOOLS.CSV":
                    spool_file = path
                elif fname.upper() == "SPOOLITEMS.CSV":
                    spool_items_file = path

            can_update_spool = spool_file and spool_items_file
            can_update_mto = bool(mto_files)

            if not can_update_spool and not can_update_mto:
                return False, (
                    "هیچ فایل معتبری انتخاب نشد.\n"
                    "برای آپدیت MTO، نام فایل باید `MTO-ProjectName.csv` باشد.\n"
                    "برای آپدیت Spool، هر دو فایل `Spools.csv` و `SpoolItems.csv` باید انتخاب شوند."
                )

            summary_log = []

            if can_update_spool:
                logging.info("Processing Spool files...")
                success, msg = self.replace_all_spool_data(spool_file, spool_items_file)
                if not success:
                    return False, f"خطا در به‌روزرسانی Spool: {msg}"
                summary_log.append(msg)

            if can_update_mto:
                for pname, mto_path in sorted(mto_files.items()):
                    logging.info(f"Processing MTO file for project '{pname}'...")
                    success, msg = self.update_project_mto_from_csv(pname, mto_path)
                    if not success:
                        return False, f"خطا در آپدیت پروژه '{pname}': {msg}. عملیات متوقف شد."
                    summary_log.append(msg)

            return True, "\n".join(summary_log)

        except Exception as e:
            import traceback
            logging.error(f"Unexpected error in process_selected_csv_files: {traceback.format_exc()}")
            return False, f"یک خطای پیش‌بینی نشده در پردازش فایل‌ها رخ داد: {e}"

    # --------------------------------------------------
    # توابع کمکی
    # --------------------------------------------------

    def _validate_and_normalize_df(self, df: pd.DataFrame, required_columns: set, file_name: str) -> pd.DataFrame:
        """
        اعتبارسنجی وجود ستون‌های ضروری و تبدیل نام ستون‌ها به فرمت یکنواخت
        """
        original_columns = df.columns
        df.columns = [str(col).strip().upper() for col in original_columns]
        missing = required_columns - set(df.columns)
        if missing:
            raise ValueError(f"فایل '{file_name}' ستون‌های ضروری زیر را ندارد: {', '.join(sorted(missing))}")
        return df

    def _normalize_and_rename_df(self, df: pd.DataFrame, column_map: dict, required_db_cols: set,
                                 file_name: str) -> pd.DataFrame:
        """
        یکسان‌سازی نام ستون‌ها و تغییر نام آنها بر اساس column_map
        """
        df = df.copy()
        df.columns = [re.sub(r'\W+', '', str(col).upper()) for col in df.columns]
        df.rename(columns={k: v for k, v in column_map.items() if k in df.columns}, inplace=True)
        missing = required_db_cols - set(df.columns)
        if missing:
            raise ValueError(f"بعد از تغییر نام ستون‌ها، ستون‌های ضروری {missing} یافت نشدند. فایل: {file_name}")
        return df

    def _normalize_line_key(self, text: str) -> str:
        """
        حذف کاراکترهای غیر مجاز و بزرگ کردن حروف برای شناسه خط
        """
        if not text:
            return ""
        return re.sub(r'[^A-Z0-9]+', '', text.upper())
