# file: data/mto_service.py
"""
سرویس MTO (Material Take-Off)
مدیریت آیتم‌های MTO، بازسازی پیشرفت خطوط، واکشی داده‌ها و پشتیبان‌گیری دیتابیس.
"""

import os
import shutil
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from data.db_session import DBSessionManager
from models import MTOItem, MTOConsumption, MTOProgress, MIVRecord, SpoolItem, SpoolConsumption
from data.constants import SPOOL_TYPE_MAPPING


class MTOService:
    def __init__(self, session_getter: callable = DBSessionManager.get_session):
        self._session_getter = session_getter

    # ----------------------------------------------------------------------
    # CRUD / FETCH METHODS
    # ----------------------------------------------------------------------

    def get_mto_item_by_id(self, mto_item_id: int) -> Optional[MTOItem]:
        """دریافت یک آیتم MTO بر اساس شناسه‌اش."""
        session = self._session_getter()
        try:
            return session.get(MTOItem, mto_item_id)
        except Exception as e:
            logging.error(f"خطا در get_mto_item_by_id({mto_item_id}): {e}")
            return None
        finally:
            session.close()

    def get_mto_items_for_line(self, project_id: int, line_no: str) -> List[MTOItem]:
        """واکشی تمام آیتم‌های MTO مربوط به یک خط خاص در یک پروژه."""
        session = self._session_getter()
        try:
            return (session.query(MTOItem)
                    .filter(MTOItem.project_id == project_id,
                            MTOItem.line_no == line_no)
                    .order_by(MTOItem.id)
                    .all())
        except Exception as e:
            logging.error(f"خطا در get_mto_items_for_line({project_id}, {line_no}): {e}")
            return []
        finally:
            session.close()

    # ----------------------------------------------------------------------
    # PROGRESS REBUILD METHOD
    # ----------------------------------------------------------------------

    def rebuild_mto_progress_for_line(self, project_id: int, line_no: str) -> None:
        """
        آمار پیشرفت تمام آیتم‌های MTO یک خط را مجدداً محاسبه و ذخیره می‌کند.
        (نسخه بهینه‌شده برای عملکرد بهتر)
        """
        session = self._session_getter()
        try:
            base_query = (
                session.query(
                    MTOItem,
                    func.coalesce(func.sum(MTOConsumption.used_qty), 0.0).label("direct_used")
                )
                .outerjoin(MTOConsumption, MTOItem.id == MTOConsumption.mto_item_id)
                .filter(MTOItem.project_id == project_id, MTOItem.line_no == line_no)
                .group_by(MTOItem.id)
            )

            mto_items_with_direct_usage = base_query.all()
            if not mto_items_with_direct_usage:
                return

            spool_consumptions_in_line = (
                session.query(
                    func.upper(SpoolItem.component_type).label("spool_type"),
                    SpoolItem.p1_bore,
                    func.sum(SpoolConsumption.used_qty).label("total_spool_used")
                )
                .join(MIVRecord, SpoolConsumption.miv_record_id == MIVRecord.id)
                .join(SpoolItem, SpoolConsumption.spool_item_id == SpoolItem.id)
                .filter(MIVRecord.project_id == project_id, MIVRecord.line_no == line_no)
                .group_by("spool_type", SpoolItem.p1_bore)
                .all()
            )

            spool_usage_map = {
                (usage.spool_type, usage.p1_bore): usage.total_spool_used
                for usage in spool_consumptions_in_line
            }

            progress_updates = []
            mto_item_ids_in_line = [item.id for item, _ in mto_items_with_direct_usage]

            for mto_item, direct_used in mto_items_with_direct_usage:
                is_pipe = mto_item.item_type and 'pipe' in mto_item.item_type.lower()
                total_required = mto_item.length_m if is_pipe else mto_item.quantity

                mto_type_upper = str(mto_item.item_type).upper().strip()
                spool_equivalents = {mto_type_upper}
                for key, aliases in SPOOL_TYPE_MAPPING.items():
                    if mto_type_upper == key or mto_type_upper in aliases:
                        spool_equivalents.update([key] + list(aliases))
                        break

                spool_used = 0
                for eq_type in spool_equivalents:
                    spool_used += spool_usage_map.get((eq_type, mto_item.p1_bore_in), 0)

                total_used = (direct_used or 0) + spool_used
                remaining = max(0, (total_required or 0) - total_used)

                progress_updates.append({
                    'mto_item_id': mto_item.id,
                    'project_id': project_id,
                    'line_no': line_no,
                    'item_code': mto_item.item_code,
                    'description': mto_item.description,
                    'unit': mto_item.unit,
                    'total_qty': round(total_required or 0, 2),
                    'used_qty': round(total_used, 2),
                    'remaining_qty': round(remaining, 2),
                    'last_updated': datetime.now()
                })

            session.query(MTOProgress).filter(
                MTOProgress.mto_item_id.in_(mto_item_ids_in_line)
            ).delete(synchronize_session=False)

            if progress_updates:
                session.bulk_insert_mappings(MTOProgress, progress_updates)

            session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در rebuild_mto_progress_for_line: {e}", exc_info=True)
        finally:
            session.close()

    # ----------------------------------------------------------------------
    # EXPORT / BACKUP
    # ----------------------------------------------------------------------

    def get_data_as_dataframe(self, project_id: Optional[int] = None) -> pd.DataFrame:
        """
        کل داده‌های MTOProgress را (برای یک پروژه خاص یا همه پروژه‌ها) به صورت DataFrame برمی‌گرداند.
        """
        session = self._session_getter()
        try:
            query = session.query(MTOProgress)
            if project_id is not None:
                query = query.filter(MTOProgress.project_id == project_id)
            df = pd.read_sql(query.statement, session.bind)
            return df
        except Exception as e:
            logging.error(f"خطا در get_data_as_dataframe: {e}", exc_info=True)
            return pd.DataFrame()
        finally:
            session.close()

    def backup_database(self, backup_dir: str) -> bool:
        """
        پشتیبان‌گیری کامل از دیتابیس MTO با کپی فایل DB به مسیر مشخص شده.
        """
        try:
            os.makedirs(backup_dir, exist_ok=True)
            # مسیر دیتابیس از Engine موجود در session getter گرفته می‌شود
            engine = DBSessionManager.engine
            db_url = str(engine.url)
            if db_url.startswith("sqlite:///"):
                src_path = db_url.replace("sqlite:///", "")
                file_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                dst_path = os.path.join(backup_dir, file_name)
                shutil.copy2(src_path, dst_path)
                logging.info(f"Backup created at {dst_path}")
                return True
            else:
                logging.error("Backup only supported for SQLite in this method.")
                return False
        except Exception as e:
            logging.error(f"خطا در backup_database: {e}", exc_info=True)
            return False
