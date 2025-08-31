# file: data_manager.py

import os
from sqlalchemy import create_engine, func, desc
from sqlalchemy.orm import sessionmaker, joinedload
from functools import lru_cache
from datetime import datetime
from models import Base, Project, MIVRecord, MTOItem, MTOConsumption, ActivityLog, MTOProgress, Spool, SpoolItem, \
    SpoolConsumption, SpoolProgress, IsoFileIndex
import numpy as np
import pandas as pd
import difflib
# data_manager.py (در ابتدای فایل)
import logging
import re
from typing import Tuple, List, Dict, Any
import glob
import time

from config_manager import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME
from sqlalchemy.exc import OperationalError
from urllib.parse import quote_plus

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SPOOL_TYPE_MAPPING = {
    "FLANGE": ("FLG", "FLAN", "FLN"),
    "ELBOW": ("ELB", "ELL", "ELBO"),
    "TEE": ("TEE",),
    "REDUCER": ("RED","REDU","CON","CONN", "ECC"),
    "CAP": ("CAP",),
    "PIPE": ("PIPE", "PIP"),

    # ... شما می‌توانید آیتم‌های بیشتری به اینجا اضافه کنید
}

class DataManager:
    def __init__(self, db_user: str | None = None, db_password: str | None = None):
        """
        مدیریت ارتباط با PostgreSQL با پشتیبانی از لاگین پویا.
        اولویت دریافت اعتبارها:
        1) آرگومان‌های ورودی
        2) Environment Variables: APP_DB_USER / APP_DB_PASSWORD
        3) config_manager (fallback نهایی)
        """
        user = (db_user or os.getenv("APP_DB_USER") or DB_USER or "").strip()
        pwd  = (db_password or os.getenv("APP_DB_PASSWORD") or DB_PASSWORD or "").strip()

        # ساخت URL امن (encode برای کاراکترهای خاص)
        user_enc = quote_plus(user)
        pwd_enc  = quote_plus(pwd)

        db_url = f"postgresql+psycopg2://{user_enc}:{pwd_enc}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

        self.engine = create_engine(
            db_url,
            echo=False,
            pool_size=10,
            max_overflow=20
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    @staticmethod
    def test_connection(db_user: str, db_password: str) -> tuple[bool, str]:
        """تست اتصال با اعتبارهای داده‌شده (بدون ایجاد آبجکت دائمی)."""
        try:
            user_enc = quote_plus(db_user.strip())
            pwd_enc = quote_plus(db_password.strip())
            db_url = f"postgresql+psycopg2://{user_enc}:{pwd_enc}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            test_engine = create_engine(db_url)
            with test_engine.connect() as conn:
                conn.execute(func.now())  # یک کوئری خیلی ساده
            return True, "OK"
        except OperationalError as e:
            return False, f"اتصال ناموفق: {e}"
        except Exception as e:
            return False, f"خطا: {e}"

    def get_session(self):
        """یک سشن جدید برای ارتباط با دیتابیس ایجاد می‌کند."""
        return self.Session()

    def log_activity(self, user, action, details="", session=None):
        """ثبت لاگ در جدول ActivityLog"""
        own_session = False
        if session is None:
            session = self.get_session()
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

    # --------------------------------------------------------------------
    # متدهای اصلی برای مدیریت رکوردها (CRUD Operations)
    # --------------------------------------------------------------------

    def register_miv_record(self, project_id, form_data, consumption_items, spool_consumption_items=None):
        session = self.get_session()
        try:
            # ... (بخش ساخت MIVRecord) ...
            new_record = MIVRecord(
                project_id=project_id,
                line_no=form_data['Line No'],
                miv_tag=form_data['MIV Tag'],
                location=form_data['Location'],
                status=form_data['Status'],
                comment=form_data.get('Comment', ''),
                registered_for=form_data['Registered For'],
                registered_by=form_data['Registered By'],
                last_updated=datetime.now(),
                is_complete=form_data.get('Complete', False)
            )
            session.add(new_record)
            session.flush()

            for item in consumption_items:
                session.add(MTOConsumption(
                    mto_item_id=item['mto_item_id'],
                    miv_record_id=new_record.id,
                    used_qty=item['used_qty'],  # از UI گرد شده می‌آید
                    timestamp=datetime.now()
                ))

            if spool_consumption_items:
                spool_notes = []
                for consumption in spool_consumption_items:
                    # ... (بخش کم کردن از موجودی اسپول بدون تغییر) ...
                    spool_item = session.get(SpoolItem, consumption['spool_item_id'])
                    used_qty = consumption['used_qty']
                    if not spool_item: raise ValueError(f"Spool item ID {consumption['spool_item_id']} not found.")
                    is_pipe = "PIPE" in (spool_item.component_type or "").upper()
                    if is_pipe:
                        if (spool_item.length or 0) < used_qty: raise ValueError(
                            f"Insufficient length for pipe in spool {spool_item.spool.spool_id}.")
                        spool_item.length -= used_qty
                    else:
                        if (spool_item.qty_available or 0) < used_qty: raise ValueError(
                            f"Insufficient qty for {spool_item.component_type} in spool {spool_item.spool.spool_id}.")
                        spool_item.qty_available -= used_qty

                    session.add(SpoolConsumption(
                        spool_item_id=spool_item.id,
                        spool_id=spool_item.spool.id,
                        miv_record_id=new_record.id,
                        used_qty=used_qty,  # از UI گرد شده می‌آید
                        timestamp=datetime.now()
                    ))

                    # --- CHANGE: اصلاح واحد در Note ---
                    unit = "m" if is_pipe else "عدد"
                    spool_notes.append(
                        f"{used_qty:.2f} {unit} از {spool_item.component_type} (اسپول: {spool_item.spool.spool_id})")

                if spool_notes:
                    final_comment = (new_record.comment or "") + " | مصرف اسپول: " + ", ".join(spool_notes)
                    new_record.comment = final_comment

            session.commit()
            self.rebuild_mto_progress_for_line(project_id, form_data['Line No'])

            self.log_activity(
                user=form_data['Registered By'], action="REGISTER_MIV",
                details=f"MIV Tag '{form_data['MIV Tag']}' for Line '{form_data['Line No']}'",
            )
            return True, "رکورد با موفقیت ثبت شد."

        except Exception as e:
            session.rollback()
            import traceback
            logging.error(f"خطا در ثبت رکورد: {e}\n{traceback.format_exc()}")
            return False, f"خطا در ثبت رکورد: {e}"
        finally:
            session.close()

    def update_miv_items(self, miv_record_id, updated_items, updated_spool_items, user="system"):
        session = self.get_session()
        try:
            record = session.get(MIVRecord, miv_record_id)
            if not record:
                return False, f"MIV با شناسه {miv_record_id} یافت نشد."

            project_id = record.project_id
            line_no = record.line_no

            # --- مدیریت مصرف اسپول ---
            # 1. بازگرداندن موجودی‌های قدیمی اسپول به انبار
            old_spool_consumptions = session.query(SpoolConsumption).filter(
                SpoolConsumption.miv_record_id == miv_record_id).all()
            for old_c in old_spool_consumptions:
                spool_item = session.get(SpoolItem, old_c.spool_item_id)
                if spool_item:
                    is_pipe = "PIPE" in (spool_item.component_type or "").upper()
                    if is_pipe:
                        spool_item.length = (spool_item.length or 0) + old_c.used_qty
                    else:
                        spool_item.qty_available = (spool_item.qty_available or 0) + old_c.used_qty

            # 2. حذف رکوردهای مصرف قدیمی (هم MTO و هم Spool)
            session.query(MTOConsumption).filter(MTOConsumption.miv_record_id == miv_record_id).delete()
            session.query(SpoolConsumption).filter(SpoolConsumption.miv_record_id == miv_record_id).delete()
            session.flush()

            # --- ثبت مصرف‌های جدید ---
            # 3. ثبت مصرف مستقیم MTO
            for item in updated_items:
                session.add(MTOConsumption(
                    mto_item_id=item["mto_item_id"],
                    miv_record_id=miv_record_id,
                    used_qty=item["used_qty"],
                    timestamp=datetime.now()
                ))

            # 4. ثبت مصرف جدید اسپول
            spool_notes = []
            if updated_spool_items:
                for s_item in updated_spool_items:
                    spool_item = session.get(SpoolItem, s_item['spool_item_id'])
                    used_qty = s_item['used_qty']

                    if not spool_item:
                        raise ValueError(f"آیتم اسپول با شناسه {s_item['spool_item_id']} یافت نشد.")

                    is_pipe = "PIPE" in (spool_item.component_type or "").upper()

                    if is_pipe:
                        if (spool_item.length or 0) < used_qty:
                            raise ValueError(f"طول موجود پایپ در اسپول {spool_item.spool.spool_id} کافی نیست.")
                        spool_item.length -= used_qty
                    else:
                        if (spool_item.qty_available or 0) < used_qty:
                            raise ValueError(
                                f"موجودی آیتم {spool_item.component_type} در اسپول {spool_item.spool.spool_id} کافی نیست.")
                        spool_item.qty_available -= used_qty

                    session.add(SpoolConsumption(
                        spool_item_id=spool_item.id,
                        spool_id=spool_item.spool.id,
                        miv_record_id=miv_record_id,
                        used_qty=used_qty,
                        timestamp=datetime.now()
                    ))
                    # ساخت Note
                    unit = "mm" if is_pipe else "عدد"
                    spool_notes.append(
                        f"{used_qty:.1f} {unit} از {spool_item.component_type} (اسپول: {spool_item.spool.spool_id})")

            # 5. (مهم) بازسازی کامل آمار خط بعد از تمام تغییرات
            session.commit()
            self.rebuild_mto_progress_for_line(project_id, line_no)

            self.log_activity(
                user=user,
                action="UPDATE_MIV_ITEMS",
                details=f"Consumption items updated for MIV {miv_record_id}",
            )
            return True, "آیتم‌های مصرفی با موفقیت بروزرسانی شدند."

        except Exception as e:
            session.rollback()
            import traceback
            logging.error(f"خطا در بروزرسانی آیتم‌های MIV {miv_record_id}: {e}\n{traceback.format_exc()}")
            return False, f"خطا در بروزرسانی آیتم‌های MIV: {e}"
        finally:
            session.close()

    def delete_miv_record(self, record_id, user="system"):
        """
        یک رکورد MIV و تمام مصرف‌های مرتبط با آن (MTO و Spool) را حذف می‌کند.
        موجودی مصرف شده از اسپول‌ها را به انبار برمی‌گرداند.
        سپس جدول MTOProgress را برای آن خط به‌طور کامل بازسازی می‌کند.
        """
        session = self.get_session()
        try:
            # ۱. رکورد اصلی را پیدا کن
            record = session.get(MIVRecord, record_id)
            if not record:
                return False, "رکورد یافت نشد."

            project_id = record.project_id
            line_no = record.line_no
            miv_tag = record.miv_tag

            # ۲. (مهم) موجودی‌های مصرفی اسپول را به انبار برگردان
            spool_consumptions = session.query(SpoolConsumption).filter(SpoolConsumption.miv_record_id == record_id).all()
            for consumption in spool_consumptions:
                spool_item = session.get(SpoolItem, consumption.spool_item_id)
                if spool_item:
                    is_pipe = "PIPE" in (spool_item.component_type or "").upper()
                    if is_pipe:
                        spool_item.length = (spool_item.length or 0) + consumption.used_qty
                    else:
                        spool_item.qty_available = (spool_item.qty_available or 0) + consumption.used_qty

            # ۳. تمام رکوردهای مصرفی مرتبط (MTO و Spool) را حذف کن
            session.query(MTOConsumption).filter(MTOConsumption.miv_record_id == record_id).delete()
            session.query(SpoolConsumption).filter(SpoolConsumption.miv_record_id == record_id).delete()

            # ۴. خود رکورد MIV را حذف کن
            session.delete(record)
            session.commit()

            # ۵. (مهم) آمار پیشرفت را برای این خط از نو بساز
            self.rebuild_mto_progress_for_line(project_id, line_no)

            # ۶. ثبت لاگ
            self.log_activity(
                user=user,
                action="DELETE_MIV",
                details=f"Deleted MIV Record ID {record_id} (Tag: {miv_tag}) for line {line_no}"
            )

            return True, "رکورد و مصرف‌های مرتبط با موفقیت حذف شدند."

        except Exception as e:
            session.rollback()
            logging.error(f"خطا در حذف رکورد MIV با شناسه {record_id}: {e}")
            return False, f"خطا در حذف رکورد: {e}"
        finally:
            session.close()

    def rebuild_mto_progress_for_line(self, project_id, line_no):
        """
        (بهینه‌سازی شده)
        آمار پیشرفت تمام آیتم‌های MTO یک خط را با استفاده از یک کوئری جامع و بهینه بازسازی می‌کند.
        """
        session = self.get_session()
        try:
            # گام ۱: تمام MTO Item های مورد نیاز را به همراه مصرف مستقیم (direct_used) واکشی می‌کنیم.
            # از left join استفاده می‌شود تا آیتم‌های بدون مصرف هم لیست شوند.
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
                return  # اگر خط هیچ آیتمی نداشت، خارج شو

            # گام ۲: تمام مصرف‌های اسپول مربوط به این خط را یک‌جا واکشی می‌کنیم.
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

            # گام ۳: مصرف اسپول را در یک دیکشنری برای دسترسی سریع آماده می‌کنیم.
            spool_usage_map = {
                (usage.spool_type, usage.p1_bore): usage.total_spool_used
                for usage in spool_consumptions_in_line
            }

            progress_updates = []
            mto_item_ids_in_line = [item.id for item, _ in mto_items_with_direct_usage]

            # گام ۴: روی نتایج واکشی شده حرکت کرده و محاسبات را در پایتون انجام می‌دهیم.
            for mto_item, direct_used in mto_items_with_direct_usage:
                is_pipe = mto_item.item_type and 'pipe' in mto_item.item_type.lower()
                total_required = mto_item.length_m if is_pipe else mto_item.quantity

                # پیدا کردن مصرف اسپول معادل
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

                # اطلاعات را برای آپدیت گروهی (bulk update) آماده می‌کنیم.
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

            # گام ۵: تمام رکوردهای قدیمی پیشرفت را حذف کرده و رکوردهای جدید را یک‌جا درج می‌کنیم.
            session.query(MTOProgress).filter(
                MTOProgress.mto_item_id.in_(mto_item_ids_in_line)
            ).delete(synchronize_session=False)

            if progress_updates:
                session.bulk_insert_mappings(MTOProgress, progress_updates)

            session.commit()
        except Exception as e:
            session.rollback()
            import traceback
            logging.error(f"خطا در rebuild_mto_progress_for_line (بهینه شده): {e}\n{traceback.format_exc()}")
        finally:
            session.close()

    def get_consumptions_for_miv(self, miv_record_id):
        """
        تمام آیتم‌های مصرفی ثبت‌شده برای یک MIV خاص را برمی‌گرداند.
        خروجی یک دیکشنری است که کلید آن mto_item_id و مقدار آن used_qty است.
        """
        session = self.get_session()
        try:
            consumptions = session.query(MTOConsumption).filter(
                MTOConsumption.miv_record_id == miv_record_id
            ).all()
            # تبدیل لیست به دیکشنری برای دسترسی سریع‌تر
            return {item.mto_item_id: item.used_qty for item in consumptions}
        except Exception as e:
            logging.error(f"Error fetching consumptions for MIV {miv_record_id}: {e}")
            return {}
        finally:
            session.close()

    # --------------------------------------------------------------------
    # متدهای جستجو و اعتبارسنجی
    # --------------------------------------------------------------------

    def is_duplicate_miv_tag(self, miv_tag, project_id):
        """بررسی می‌کند که آیا یک MIV Tag در یک پروژه خاص تکراری است یا خیر."""
        session = self.get_session()
        try:
            exists = session.query(MIVRecord.id).filter(
                MIVRecord.project_id == project_id,
                MIVRecord.miv_tag == miv_tag
            ).first()
            return exists is not None
        finally:
            session.close()

    def get_line_no_suggestions(self, typed_text: str, top_n: int = 15) -> List[Dict[str, Any]]:
        """
        (نسخه بهینه‌سازی شده با استفاده از LIKE)
        در تمام پروژه‌ها جستجو کرده و شماره خط‌های مشابه را به همراه نام پروژه پیشنهاد می‌دهد.
        این جستجو مستقیماً در دیتابیس و با سرعت بالا انجام می‌شود.
        """
        if not typed_text or len(typed_text) < 2:
            return []

        session = self.get_session()
        try:
            # ساخت عبارت جستجو برای اپراتور LIKE
            search_term = f"%{typed_text}%"

            # کوئری بهینه که فیلتر را در دیتابیس اعمال می‌کند
            query = (
                session.query(
                    MTOItem.line_no,
                    Project.name,
                    Project.id
                )
                .join(Project, MTOItem.project_id == Project.id)
                .filter(MTOItem.line_no.ilike(search_term))  # ilike برای جستجوی غیرحساس به حروف
                .distinct()
                .limit(top_n)
            )

            results = query.all()

            # تبدیل نتایج به فرمت دیکشنری مورد نیاز UI
            suggestions = [
                {
                    'display': f"{line_no}  ({project_name})",
                    'line_no': line_no,
                    'project_name': project_name,
                    'project_id': project_id
                }
                for line_no, project_name, project_id in results
            ]
            return suggestions

        except Exception as e:
            logging.error(f"خطا در پیشنهاد سراسری شماره خط (بهینه شده): {e}")
            return []
        finally:
            session.close()

    def search_miv_by_line_no(self, project_id, line_no):
        """تمام رکوردهای MIV مربوط به یک شماره خط در یک پروژه را برمی‌گرداند."""
        session = self.get_session()
        try:
            records = session.query(MIVRecord).filter(
                MIVRecord.project_id == project_id,
                MIVRecord.line_no == line_no
            ).all()
            return records
        finally:
            session.close()

    def get_mto_item_by_id(self, mto_item_id: int):
        """یک آیتم MTO را بر اساس شناسه اصلی آن برمی‌گرداند."""
        session = self.get_session()
        try:
            # از session.get برای دسترسی مستقیم و سریع به آیتم با ID استفاده می‌کنیم
            return session.get(MTOItem, mto_item_id)
        except Exception as e:
            logging.error(f"Error fetching MTO item with id {mto_item_id}: {e}")
            return None
        finally:
            session.close()

    # --------------------------------------------------------------------
    # متدهای دریافت داده برای نمایش (Viewers & Tables)
    # --------------------------------------------------------------------

    def get_miv_data(self, project_id, mode='all', line_no=None, last_n=None):
        """داده‌های MIV را برای نمایش در جدول بر اساس حالت‌های مختلف برمی‌گرداند."""
        session = self.get_session()
        try:
            query = session.query(MIVRecord).filter(MIVRecord.project_id == project_id)

            if mode == 'complete':
                query = query.filter(MIVRecord.is_complete == True)
            elif mode == 'incomplete':
                query = query.filter(MIVRecord.is_complete == False)

            if line_no:
                query = query.filter(MIVRecord.line_no == line_no)

            if last_n:
                query = query.order_by(MIVRecord.last_updated.desc()).limit(last_n)

            return query.all()
        finally:
            session.close()

    def get_mto_items_for_line(self, project_id, line_no):
        """تمام آیتم‌های MTO برای یک شماره خط خاص را برمی‌گرداند."""
        session = self.get_session()
        try:
            # این کوئری تمام اطلاعات لازم برای پنجره مصرف را برمی‌گرداند
            items = session.query(MTOItem).filter(
                MTOItem.project_id == project_id,
                MTOItem.line_no == line_no
            ).all()
            return items
        finally:
            session.close()

    def get_all_projects(self):
        """لیست همه پروژه‌ها را برمی‌گرداند."""
        session = self.get_session()
        try:
            return session.query(Project).order_by(Project.name).all()
        finally:
            session.close()

    def get_project_by_name(self, name):
        """پروژه را بر اساس نام دقیق آن جستجو می‌کند."""
        session = self.get_session()
        try:
            return session.query(Project).filter(Project.name == name).first()
        finally:
            session.close()

    # --------------------------------------------------------------------
    # متدهای گزارش‌گیری (مربوط به داشبورد و گزارش‌ها)
    # --------------------------------------------------------------------
    # متدهای get_project_progress, get_line_progress و generate_project_report که قبلاً نوشته‌اید
    # در اینجا قرار می‌گیرند و کامل هستند.
    @lru_cache(maxsize=128)
    def get_project_progress(self, project_id, default_diameter=1):
        """
        محاسبه پیشرفت کلی پروژه بر اساس داده‌های دیتابیس
        - وزن هر خط = (مجموع LENGTH(M) + QUANTITY) × بیشترین قطر پایپ در آن خط
        - درصد پیشرفت = وزن انجام‌شده / وزن کل × 100
        """
        from models import MTOItem, MTOConsumption, MIVRecord

        session = self.get_session()
        try:
            # گرفتن تمام شماره خطوط پروژه
            lines = session.query(MTOItem.line_no).filter(MTOItem.project_id == project_id).distinct().all()
            if not lines:
                return {"total_lines": 0, "total_weight": 0, "done_weight": 0, "percentage": 0}

            total_weight = 0
            done_weight = 0

            for (line_no,) in lines:
                # گرفتن تمام آیتم‌های این خط
                items = session.query(MTOItem).filter(
                    MTOItem.project_id == project_id,
                    MTOItem.line_no == line_no
                ).all()

                if not items:
                    continue

                # بیشترین قطر پایپ در این خط (در MTOItem پیکسلی نیست ولی میشه به item_type استناد کرد)
                max_diameter = default_diameter
                for item in items:
                    if item.item_type and "pipe" in item.item_type.lower():
                        try:
                            # فرض: طول یا قطر پایپ در description یا unit ذخیره نشده، فعلاً پیش‌فرض می‌زنیم
                            pass
                        except:
                            pass

                # مجموع مقادیر طول و تعداد
                length_sum = sum(item.length_m or 0 for item in items)
                qty_sum = sum(item.quantity or 0 for item in items)
                qty_sum_effective = length_sum + qty_sum

                line_weight = qty_sum_effective * max_diameter
                total_weight += line_weight

                # محاسبه مصرف‌شده
                used_qty = (
                               session.query(func.coalesce(func.sum(MTOConsumption.used_qty), 0))
                               .join(MTOItem, MTOConsumption.mto_item_id == MTOItem.id)
                               .filter(MTOItem.project_id == project_id, MTOItem.line_no == line_no)
                               .scalar()
                           ) or 0

                done_weight += used_qty * max_diameter

            percentage = round((done_weight / total_weight * 100), 2) if total_weight > 0 else 0

            return {
                "total_lines": len(lines),
                "total_weight": total_weight,
                "done_weight": done_weight,
                "percentage": percentage
            }

        except Exception as e:
            print(f"⚠️ خطا در محاسبه پیشرفت پروژه: {e}")
            return {"total_lines": 0, "total_weight": 0, "done_weight": 0, "percentage": 0}
        finally:
            session.close()

    @lru_cache(maxsize=256)
    def get_line_progress(self, project_id, line_no, readonly=True):  # 🔹 نیازی به default_diameter نیست
        """
        محاسبه پیشرفت یک خط خاص در پروژه با استفاده از داده‌های MTOProgress.
        """
        session = self.get_session()
        try:
            # جمع کل و مصرف شده از رکوردهای MTOProgress
            # ما فرض می‌کنیم هر آیتم وزن یکسانی دارد. اگر وزن‌دهی پیچیده‌تر نیاز بود، منطق تغییر می‌کند.
            query_result = session.query(
                func.sum(MTOProgress.total_qty),
                func.sum(MTOProgress.used_qty)
            ).filter(
                MTOProgress.project_id == project_id,
                MTOProgress.line_no == line_no
            ).first()

            total_weight, done_weight = query_result
            total_weight = total_weight or 0
            done_weight = done_weight or 0

            if total_weight == 0 and not readonly:
                # اگر خط داده‌ای در MTOProgress نداشت، یک بار آن را بساز
                self.initialize_mto_progress_for_line(project_id, line_no)
                query_result = session.query(
                    func.sum(MTOProgress.total_qty),
                    func.sum(MTOProgress.used_qty)
                ).filter(
                    MTOProgress.project_id == project_id,
                    MTOProgress.line_no == line_no
                ).first()
                total_weight, done_weight = query_result
                total_weight = total_weight or 0
                done_weight = done_weight or 0

            percentage = round((done_weight / total_weight * 100), 2) if total_weight > 0 else 0

            return {
                "line_no": line_no,
                "total_weight": total_weight,
                "done_weight": done_weight,
                "percentage": percentage
            }

        except Exception as e:
            print(f"⚠️ خطا در محاسبه پیشرفت خط {line_no}: {e}")
            return {"line_no": line_no, "total_weight": 0, "done_weight": 0, "percentage": 0}
        finally:
            session.close()

    def generate_project_report(self, project_id):
        """
        تولید گزارش کامل پیشرفت پروژه
        شامل درصد پیشرفت کلی و جزئیات هر خط
        """
        report = {
            "project_id": project_id,
            "summary": self.get_project_progress(project_id),
            "lines": []
        }

        # گرفتن شماره تمام خطوط پروژه
        from models import MTOItem
        session = self.get_session()
        try:
            lines = (
                session.query(MTOItem.line_no)
                .filter(MTOItem.project_id == project_id)
                .distinct()
                .all()
            )
            for (line_no,) in lines:
                line_progress = self.get_line_progress(project_id, line_no)
                report["lines"].append(line_progress)

            # ثبت گزارش به عنوان فعالیت
            self.log_activity(
                user="system",  # یا نام کاربری که لاگین کرده
                action="GENERATE_REPORT",
                details=f"Generated progress report for project ID {project_id}"
            )

            return report

        except Exception as e:
            print(f"⚠️ خطا در تولید گزارش پروژه {project_id}: {e}")
            return report
        finally:
            session.close()

        # متدهای مدیریت پروژه و اعتبارسنجی (باقی‌مانده از MIVRegistry)
        # --------------------------------------------------------------------

    def rename_project(self, project_id, new_name, user="system"):
        """نام یک پروژه را تغییر می‌دهد."""
        session = self.get_session()
        try:
            project = session.query(Project).filter(Project.id == project_id).first()
            if project:
                original_name = project.name
                # بررسی اینکه نام جدید تکراری نباشد
                name_exists = session.query(Project.id).filter(Project.name == new_name).first()
                if name_exists:
                    return False, f"پروژه‌ای با نام '{new_name}' از قبل وجود دارد."

                project.name = new_name
                session.commit()
                self.log_activity(user, "RENAME_PROJECT", f"Project '{original_name}' renamed to '{new_name}'")
                return True, f"نام پروژه با موفقیت به '{new_name}' تغییر یافت."
            return False, "پروژه یافت نشد."
        except Exception as e:
            session.rollback()
            return False, f"خطا در تغییر نام پروژه: {e}"
        finally:
            session.close()

    def copy_line_to_project(self, line_no, from_project_id, to_project_id, user="system"):
        """تمام رکوردهای MIV یک خط را از پروژه‌ای به پروژه دیگر کپی می‌کند."""
        session = self.get_session()
        try:
            records_to_copy = session.query(MIVRecord).filter(
                MIVRecord.project_id == from_project_id,
                MIVRecord.line_no == line_no
            ).all()

            if not records_to_copy:
                return False, "هیچ رکوردی برای کپی یافت نشد."

            for record in records_to_copy:
                # برای جلوگیری از تکراری شدن تگ، یک پسوند به آن اضافه می‌کنیم
                new_tag = f"{record.miv_tag}-COPY-{datetime.now().strftime('%f')}"

                new_record = MIVRecord(
                    project_id=to_project_id,
                    line_no=record.line_no,
                    miv_tag=new_tag,
                    location=record.location,
                    status=record.status,
                    comment=f"Copied from project ID {from_project_id}",
                    registered_for=record.registered_for,
                    registered_by=user,
                    is_complete=record.is_complete,
                    last_updated=datetime.now()
                )
                session.add(new_record)

            session.commit()
            self.log_activity(user, "COPY_LINE",
                              f"Line '{line_no}' copied from project {from_project_id} to {to_project_id}")
            return True, "خط با موفقیت کپی شد."
        except Exception as e:
            session.rollback()
            return False, f"خطا در کپی کردن خط: {e}"
        finally:
            session.close()

    def check_duplicates_in_project(self, project_id, column_name='miv_tag'):
        """رکوردهای با مقدار تکراری در یک ستون خاص (مثل miv_tag) را پیدا می‌کند."""
        session = self.get_session()
        try:
            if not hasattr(MIVRecord, column_name):
                return None, f"ستونی با نام '{column_name}' در مدل MIVRecord وجود ندارد."

            column = getattr(MIVRecord, column_name)

            # پیدا کردن مقادیر تکراری
            duplicates_query = session.query(column, func.count(MIVRecord.id).label('count')). \
                filter(MIVRecord.project_id == project_id). \
                group_by(column). \
                having(func.count(MIVRecord.id) > 1).subquery()

            # گرفتن تمام رکوردهایی که مقدار ستونشان تکراری است
            final_query = session.query(MIVRecord).join(
                duplicates_query, column == duplicates_query.c[column_name]
            ).order_by(column)

            return final_query.all(), None
        except Exception as e:
            return None, f"خطا در بررسی موارد تکراری: {e}"
        finally:
            session.close()

    def is_line_complete(self, project_id, line_no):
        session = self.get_session()
        try:
            mto_items = session.query(MTOItem).filter(
                MTOItem.project_id == project_id,
                MTOItem.line_no == line_no
            ).all()

            if not mto_items:
                return False

            for item in mto_items:
                if item.item_type and 'pipe' in item.item_type.lower():
                    total_required = item.length_m or 0
                else:
                    total_required = item.quantity or 0

                total_used = session.query(func.coalesce(func.sum(MTOConsumption.used_qty), 0.0)) \
                    .filter(MTOConsumption.mto_item_id == item.id).scalar()

                if total_used < total_required:
                    return False
            return True
        finally:
            session.close()

    def get_enriched_line_progress(self, project_id, line_no, readonly=True):
        """
        داده‌های پیشرفت متریال یک خط را به همراه اطلاعات تکمیلی از MTOItem برمی‌گرداند.
        """
        session = self.get_session()
        try:
            # اگر هیچ رکوردی در جدول پیشرفت نبود، آن را از MTOItem بساز
            if not readonly:
                self.initialize_mto_progress_for_line(project_id, line_no)

            # جوین MTOProgress با MTOItem برای گرفتن اطلاعات بیشتر
            results = session.query(
                MTOProgress,
                MTOItem.p1_bore_in,
                MTOItem.item_type
            ).join(
                MTOItem, MTOProgress.mto_item_id == MTOItem.id
            ).filter(
                MTOProgress.project_id == project_id,
                MTOProgress.line_no == line_no
            ).all()

            progress_data = []
            for item in results:
                progress_record, p1_bore, item_type = item
                progress_data.append({
                    "mto_item_id": progress_record.mto_item_id,
                    "Item Code": progress_record.item_code,
                    "Description": progress_record.description,
                    "Unit": progress_record.unit,
                    "Total Qty": progress_record.total_qty or 0,
                    "Used Qty": progress_record.used_qty or 0,
                    "Remaining Qty": progress_record.remaining_qty or 0,
                    "Bore": p1_bore,
                    "Type": item_type
                })
            return progress_data
        except Exception as e:
            logging.error(f"Error in get_enriched_line_progress for line {line_no}: {e}")
            return []
        finally:
            session.close()

    def initialize_mto_progress_for_line(self, project_id, line_no):
        session = self.get_session()
        try:
            mto_items = session.query(MTOItem).filter(
                MTOItem.project_id == project_id,
                MTOItem.line_no == line_no
            ).all()

            for item in mto_items:
                exists = session.query(MTOProgress).filter(MTOProgress.mto_item_id == item.id).first()
                if not exists:
                    # --- CHANGE: حذف تبدیل واحد ---
                    is_pipe = item.item_type and 'pipe' in item.item_type.lower()
                    if is_pipe:
                        total_required = item.length_m or 0 # دیگر ضرب در ۱۰۰۰ نداریم
                    else:
                        total_required = item.quantity or 0

                    total_used = session.query(func.coalesce(func.sum(MTOConsumption.used_qty), 0.0)) \
                        .filter(MTOConsumption.mto_item_id == item.id).scalar()

                    new_progress = MTOProgress(
                        project_id=project_id,
                        line_no=line_no,
                        mto_item_id=item.id,
                        item_code=item.item_code,
                        description=item.description,
                        unit=item.unit,
                        total_qty=round(total_required, 2),
                        used_qty=round(total_used, 2),
                        remaining_qty=round(max(0, total_required - total_used), 2),
                        last_updated=datetime.now()
                    )
                    session.add(new_progress)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"⚠️ خطا در ساخت پیش‌فرض MTO Progress: {e}")
        finally:
            session.close()

    def get_data_as_dataframe(self, model_class, project_id=None):
        """
        داده‌های یک جدول (مدل) را به صورت پانداز DataFrame برمی‌گرداند.
        این متد برای خروجی گرفتن اکسل بسیار مفید است.
        """
        session = self.get_session()
        try:
            query = session.query(model_class)
            if project_id and hasattr(model_class, 'project_id'):
                query = query.filter(model_class.project_id == project_id)

            # استفاده از pd.read_sql برای کارایی بهتر
            df = pd.read_sql(query.statement, session.bind)
            return df
        except Exception as e:
            print(f"Error converting table to DataFrame: {e}")
            return pd.DataFrame()
        finally:
            session.close()

    def backup_database(self, backup_dir="."):
        """از کل فایل پایگاه داده یک نسخه پشتیبان تهیه می‌کند."""
        import shutil

        db_file = self.engine.url.database
        if not os.path.exists(db_file):
            return False, "فایل پایگاه داده یافت نشد."

        try:
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)

            backup_name = f"backup_{os.path.basename(db_file)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            backup_path = os.path.join(backup_dir, backup_name)

            shutil.copy2(db_file, backup_path)
            return True, f"پشتیبان‌گیری با موفقیت در مسیر {backup_path} انجام شد."
        except Exception as e:
            return False, f"خطا در پشتیبان‌گیری: {e}"

    def update_mto_progress(self, project_id, line_no, updates):
        """
        بروزرسانی جدول mto_progress بر اساس آیتم‌های مصرفی جدید.
        updates: لیستی از تاپل‌ها (item_code, qty, unit, description)
        """
        session = self.get_session()
        try:
            for item_code, qty, unit, desc in updates:
                # پیدا کردن آیتم‌ها از MTOItem
                query = session.query(MTOItem).filter(
                    MTOItem.project_id == project_id,
                    MTOItem.line_no == line_no
                )

                if item_code and str(item_code).strip():
                    query = query.filter(MTOItem.item_code == str(item_code).strip())
                else:
                    query = query.filter(MTOItem.description == str(desc).strip())

                mto_items = query.all()

                # محاسبه Total Qty
                total_qty = 0
                for mto_item in mto_items:
                    if mto_item.item_type and "pipe" in mto_item.item_type.lower():
                        total_qty += mto_item.length_m or 0
                    else:
                        total_qty += mto_item.quantity or 0

                # محاسبه Used Qty
                used_qty = 0
                for mto_item in mto_items:
                    used_qty += (
                                    session.query(func.coalesce(func.sum(MTOConsumption.used_qty), 0.0))
                                    .filter(MTOConsumption.mto_item_id == mto_item.id)
                                    .scalar()
                                ) or 0

                remaining_qty = max(0, total_qty - used_qty)

                # پیدا کردن یا ساخت رکورد در MTOProgress
                progress_row = session.query(MTOProgress).filter(
                    MTOProgress.line_no == line_no,
                    MTOProgress.item_code == (item_code or "")
                ).first()

                if progress_row:
                    progress_row.total_qty = total_qty
                    progress_row.used_qty = used_qty
                    progress_row.remaining_qty = remaining_qty
                    progress_row.last_updated = datetime.now()
                else:
                    new_progress = MTOProgress(
                        line_no=line_no,
                        item_code=item_code or "",
                        description=desc,
                        unit=unit,
                        total_qty=total_qty,
                        used_qty=used_qty,
                        remaining_qty=remaining_qty,
                        last_updated=datetime.now()
                    )
                    session.add(new_progress)

            session.commit()
        except Exception as e:
            session.rollback()
            print(f"⚠️ خطا در بروزرسانی mto_progress: {e}")
        finally:
            session.close()

    def get_used_qty(self, project_id, line_no, item_code=None, description=None):
        session = self.get_session()
        try:
            query = session.query(func.coalesce(func.sum(MTOConsumption.used_qty), 0.0)) \
                .join(MTOItem, MTOConsumption.mto_item_id == MTOItem.id) \
                .filter(MTOItem.project_id == project_id, MTOItem.line_no == line_no)

            if item_code and str(item_code).strip():
                query = query.filter(MTOItem.item_code == str(item_code).strip())
            elif description:
                query = query.filter(MTOItem.description == str(description).strip())

            return query.scalar() or 0
        finally:
            session.close()

    def suggest_line_no(self, project_id, line_no_input):
        session = self.get_session()
        try:
            all_lines = [x[0] for x in session.query(MTOItem.line_no)
            .filter(MTOItem.project_id == project_id)
            .distinct().all()]
            norm_input = str(line_no_input).replace(" ", "").lower()
            normalized_lines = {line: str(line).replace(" ", "").lower() for line in all_lines}

            best_match = None
            best_ratio = 0
            for original, normalized in normalized_lines.items():
                ratio = difflib.SequenceMatcher(None, norm_input, normalized).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = original

            return best_match if best_ratio > 0.6 else None
        finally:
            session.close()

    # --------------------------------------------------------------------
    # متدهایی که در API جدید صدا زدیم
    # --------------------------------------------------------------------

    def get_lines_for_project(self, project_id):
        """تمام شماره خط‌های متمایز برای یک پروژه را برمی‌گرداند."""
        session = self.get_session()
        try:
            # از جدول MTOItem شماره خطوط را می‌خوانیم
            lines = session.query(MTOItem.line_no).filter(MTOItem.project_id == project_id).distinct().order_by(
                MTOItem.line_no).all()
            # نتیجه کوئری لیستی از tupleهاست، آن را به لیست رشته تبدیل می‌کنیم
            return [line[0] for line in lines]
        except Exception as e:
            logging.error(f"Error fetching lines for project {project_id}: {e}")
            return []
        finally:
            session.close()

    def get_activity_logs(self, limit=100):
        """آخرین N رکورد از جدول لاگ فعالیت‌ها را برمی‌گرداند."""
        session = self.get_session()
        try:
            return session.query(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(limit).all()
        except Exception as e:
            logging.error(f"Error fetching activity logs: {e}")
            return []
        finally:
            session.close()

    def get_project_analytics(self, project_id):
        """داده‌های تحلیلی و آماری یک پروژه را برای داشبورد استخراج می‌کند."""
        session = self.get_session()
        try:
            # 1. تحلیل فعالیت کاربران (تعداد MIV ثبت شده توسط هر کاربر)
            user_activity = session.query(
                MIVRecord.registered_by,
                func.count(MIVRecord.id).label('miv_count')
            ).filter(MIVRecord.project_id == project_id).group_by(MIVRecord.registered_by).order_by(
                func.count(MIVRecord.id).desc()).all()

            # 2. تحلیل مصرف متریال (پر مصرف‌ترین آیتم‌ها)
            material_consumption = session.query(
                MTOItem.description,
                func.sum(MTOConsumption.used_qty).label('total_used')
            ).join(MTOConsumption, MTOItem.id == MTOConsumption.mto_item_id) \
                .filter(MTOItem.project_id == project_id) \
                .group_by(MTOItem.description).order_by(func.sum(MTOConsumption.used_qty).desc()).limit(10).all()

            # 3. تحلیل وضعیت MIV ها
            status_distribution = session.query(
                MIVRecord.status,
                func.count(MIVRecord.id).label('status_count')
            ).filter(MIVRecord.project_id == project_id, MIVRecord.status != None) \
                .group_by(MIVRecord.status).all()

            return {
                "user_activity": [{"user": user, "count": count} for user, count in user_activity],
                "material_consumption": [{"material": desc, "total_used": used} for desc, used in material_consumption],
                "status_distribution": [{"status": status, "count": count} for status, count in status_distribution]
            }
        except Exception as e:
            logging.error(f"Error fetching project analytics for project {project_id}: {e}")
            return {}
        finally:
            session.close()

    # --------------------------------------------------------------------
    # متدهای لازم برای گذارش گیری
    # --------------------------------------------------------------------

    def get_project_mto_summary(self, project_id: int, **filters) -> Dict[str, Any]:
        """
        --- CHANGE: بازنویسی کامل برای افزودن فیلترهای پیشرفته و خلاصه‌سازی ---
        گزارش خلاصه پیشرفت متریال (MTO Summary) را برای کل پروژه تولید می‌کند.
        """
        session = self.get_session()
        try:
            # کوئری پایه برای جمع‌بندی پیشرفت در سطح پروژه
            summary_query = session.query(
                MTOProgress.item_code,
                MTOProgress.description,
                MTOProgress.unit,
                func.sum(MTOProgress.total_qty).label("total_required"),
                func.sum(MTOProgress.used_qty).label("total_used")
            ).filter(MTOProgress.project_id == project_id).group_by(
                MTOProgress.item_code, MTOProgress.description, MTOProgress.unit
            )

            # --- Filters ---
            if filters.get('item_code'):
                summary_query = summary_query.filter(MTOProgress.item_code.ilike(f"%{filters['item_code']}%"))
            if filters.get('description'):
                summary_query = summary_query.filter(MTOProgress.description.ilike(f"%{filters['description']}%"))

            # اجرای کوئری برای گرفتن تمام نتایج فیلتر شده
            all_results = summary_query.all()

            report_data = []
            total_required_sum = 0
            total_used_sum = 0

            for row in all_results:
                total_required_sum += row.total_required
                total_used_sum += row.total_used
                remaining = row.total_required - row.total_used
                progress = (row.total_used / row.total_required * 100) if row.total_required > 0 else 0

                # فیلترهای بعد از محاسبه (برای پیشرفت)
                min_progress = filters.get('min_progress')
                max_progress = filters.get('max_progress')
                if (min_progress is not None and progress < min_progress) or \
                        (max_progress is not None and progress > max_progress):
                    continue

                report_data.append({
                    "Item Code": row.item_code or "N/A",
                    "Description": row.description,
                    "Unit": row.unit,
                    "Total Required": round(row.total_required, 2),
                    "Total Used": round(row.total_used, 2),
                    "Remaining": round(remaining, 2),
                    "Progress (%)": round(progress, 2)
                })

            # مرتب‌سازی نهایی
            sort_by = filters.get('sort_by', 'Item Code')
            sort_order = filters.get('sort_order', 'asc')
            reverse = sort_order == 'desc'
            if sort_by in report_data[0]:
                report_data.sort(key=lambda x: x[sort_by], reverse=reverse)

            # ساخت دیکشنری خروجی نهایی
            output = {
                "summary": {
                    "total_unique_items": len(report_data),
                    "grand_total_required": round(total_required_sum, 2),
                    "grand_total_used": round(total_used_sum, 2),
                    "overall_progress": round(
                        (total_used_sum / total_required_sum * 100) if total_required_sum > 0 else 0, 2)
                },
                "data": report_data
            }
            return output

        except Exception as e:
            logging.error(f"Error in get_project_mto_summary: {e}")
            return {"summary": {}, "data": []}
        finally:
            session.close()

    def get_project_line_status_list(self, project_id: int) -> List[Dict[str, Any]]:
        """
        گزارش لیست وضعیت خطوط (Line Status List) را برای یک پروژه تولید می‌کند.
        """
        session = self.get_session()
        try:
            lines = self.get_lines_for_project(project_id)
            report_data = []
            for line_no in lines:
                progress_info = self.get_line_progress(project_id, line_no)
                last_activity = session.query(func.max(MIVRecord.last_updated)).filter(
                    MIVRecord.project_id == project_id,
                    MIVRecord.line_no == line_no
                ).scalar()

                status = "Complete" if progress_info.get("percentage", 0) >= 99.99 else "In-Progress"

                report_data.append({
                    "Line No": line_no,
                    "Progress (%)": progress_info.get("percentage", 0),
                    "Status": status,
                    "Last Activity Date": last_activity.strftime('%Y-%m-%d') if last_activity else "N/A"
                })
            return sorted(report_data, key=lambda x: x['Line No'])
        except Exception as e:
            logging.error(f"Error in get_project_line_status_list: {e}")
            return []
        finally:
            session.close()

    def get_detailed_line_report(self, project_id: int, line_no: str) -> Dict[str, List]:
        """
        گزارش کامل و دو بخشی یک خط خاص را تولید می‌کند.
        """
        session = self.get_session()
        try:
            # بخش اول: لیست متریال (Bill of Materials)
            bom = self.get_enriched_line_progress(project_id, line_no,
                                                  readonly=False)  # Readonly=False to ensure it's initialized

            # بخش دوم: تاریخچه MIV ها
            miv_history_query = session.query(MIVRecord).filter(
                MIVRecord.project_id == project_id,
                MIVRecord.line_no == line_no
            ).order_by(desc(MIVRecord.last_updated)).all()

            miv_history = [
                {
                    "MIV Tag": r.miv_tag,
                    "Registered By": r.registered_by,
                    "Date": r.last_updated.strftime('%Y-%m-%d %H:%M'),
                    "Status": r.status,
                    "Comment": r.comment
                } for r in miv_history_query
            ]

            return {
                "bill_of_materials": bom,
                "miv_history": miv_history
            }
        except Exception as e:
            logging.error(f"Error in get_detailed_line_report: {e}")
            return {"bill_of_materials": [], "miv_history": []}
        finally:
            session.close()

    def get_shortage_report(self, project_id: int, line_no: str = None) -> Dict[str, Any]:
        """
        گزارش کسری متریال را تولید می‌کند.
        --- CHANGE: خروجی به دیکشنری تغییر یافت تا با سایر گزارش‌ها سازگار باشد ---
        """
        session = self.get_session()
        try:
            # ... (منطق داخلی تابع بدون تغییر باقی می‌ماند) ...
            query = session.query(
                MTOProgress.item_code,
                MTOProgress.description,
                MTOProgress.unit,
                func.sum(MTOProgress.total_qty).label("total_required"),
                func.sum(MTOProgress.used_qty).label("total_used")
            ).filter(MTOProgress.project_id == project_id)

            if line_no:
                query = query.filter(MTOProgress.line_no == line_no)

            results = query.group_by(
                MTOProgress.item_code, MTOProgress.description, MTOProgress.unit
            ).having(
                func.sum(MTOProgress.total_qty) > func.sum(MTOProgress.used_qty)
            ).order_by(MTOProgress.item_code).all()

            report_data = []
            for row in results:
                remaining = row.total_required - row.total_used
                progress = (row.total_used / row.total_required * 100) if row.total_required > 0 else 0
                report_data.append({
                    "Item Code": row.item_code or "N/A",
                    "Description": row.description,
                    "Unit": row.unit,
                    "Total Required": round(row.total_required, 2),
                    "Total Used": round(row.total_used, 2),
                    "Remaining": round(remaining, 2),
                    "Progress (%)": round(progress, 2)
                })

            # BUG FIX: برگرداندن نتیجه در قالب دیکشنری
            return {"data": report_data}
        except Exception as e:
            logging.error(f"Error in get_shortage_report: {e}")
            return {"data": []}
        finally:
            session.close()


    def get_spool_inventory_report(self, **filters) -> Dict[str, Any]:
        """
        --- CHANGE: بازنویسی کامل برای افزودن فیلتر، مرتب‌سازی و صفحه‌بندی ---
        گزارش موجودی انبار اسپول را تولید می‌کند.
        """
        session = self.get_session()
        try:
            query = session.query(Spool, SpoolItem).join(
                SpoolItem, Spool.id == SpoolItem.spool_id_fk
            ).filter(
                (SpoolItem.qty_available > 0.001) | (SpoolItem.length > 0.001)
            )

            # --- Filters ---
            if filters.get('spool_id'):
                query = query.filter(Spool.spool_id.ilike(f"%{filters['spool_id']}%"))
            if filters.get('location'):
                query = query.filter(Spool.location.ilike(f"%{filters['location']}%"))
            if filters.get('component_type'):
                query = query.filter(SpoolItem.component_type.ilike(f"%{filters['component_type']}%"))
            if filters.get('material'):
                query = query.filter(SpoolItem.material.ilike(f"%{filters['material']}%"))

            # --- Sorting ---
            sort_by = filters.get('sort_by', 'spool_id')
            sort_order = filters.get('sort_order', 'asc')
            sort_column = getattr(Spool, sort_by, getattr(SpoolItem, sort_by, Spool.spool_id))
            query = query.order_by(desc(sort_column) if sort_order == 'desc' else sort_column)

            # --- Pagination ---
            page = filters.get('page', 1)
            per_page = filters.get('per_page', 20)
            total_records = query.count()
            total_pages = (total_records + per_page - 1) // per_page

            results = query.offset((page - 1) * per_page).limit(per_page).all()

            report_data = []
            for spool, item in results:
                is_pipe = "PIPE" in (item.component_type or "").upper()
                report_data.append({
                    "Spool ID": spool.spool_id,
                    "Location": spool.location,
                    "Component Type": item.component_type,
                    "Item Code": item.item_code,
                    "Material": item.material,
                    "Bore1": item.p1_bore,
                    "Schedule": item.schedule,
                    "Available": round(item.length if is_pipe else item.qty_available, 2),
                    "Unit": "m" if is_pipe else "pcs"
                })

            return {
                "pagination": {
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "current_page": page,
                    "per_page": per_page
                },
                "data": report_data
            }
        except Exception as e:
            logging.error(f"Error in get_spool_inventory_report: {e}")
            return {"pagination": {}, "data": []}
        finally:
            session.close()

    def get_spool_consumption_history(self) -> List[Dict[str, Any]]:
        """
        گزارش تاریخچه مصرف اسپول‌ها را تولید می‌کند.
        """
        session = self.get_session()
        try:
            history_query = session.query(
                SpoolConsumption.timestamp,
                Spool.spool_id,
                SpoolItem.component_type,
                SpoolConsumption.used_qty,
                MIVRecord.miv_tag,
                MIVRecord.line_no
            ).join(
                SpoolItem, SpoolConsumption.spool_item_id == SpoolItem.id
            ).join(
                Spool, SpoolConsumption.spool_id == Spool.id
            ).join(
                MIVRecord, SpoolConsumption.miv_record_id == MIVRecord.id
            ).order_by(desc(SpoolConsumption.timestamp)).all()

            report_data = []
            for row in history_query:
                is_pipe = "PIPE" in (row.component_type or "").upper()
                unit = "m" if is_pipe else "pcs"
                report_data.append({
                    "Timestamp": row.timestamp.strftime('%Y-%m-%d %H:%M'),
                    "Spool ID": row.spool_id,
                    "Component Type": row.component_type,
                    "Used Qty": f"{row.used_qty:.2f} {unit}",
                    "Consumed in MIV": row.miv_tag,
                    "For Line No": row.line_no
                })
            return report_data
        except Exception as e:
            logging.error(f"Error in get_spool_consumption_history: {e}")
            return []
        finally:
            session.close()

    def get_report_analytics(self, project_id: int, report_name: str, **params) -> Dict[str, Any]:
        """
        --- NEW: متد جدید و قدرتمند برای تولید داده‌های تحلیلی و آماری برای نمودارها ---
        """
        session = self.get_session()
        try:
            # گزارش اول: توزیع پیشرفت خطوط (برای نمودار میله‌ای یا دایره‌ای)
            if report_name == 'line_progress_distribution':
                lines = self.get_project_line_status_list(project_id)
                bins = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-99%": 0, "100%": 0}
                for line in lines:
                    p = line['Progress (%)']
                    if p < 25:
                        bins["0-25%"] += 1
                    elif p < 50:
                        bins["25-50%"] += 1
                    elif p < 75:
                        bins["50-75%"] += 1
                    elif p < 100:
                        bins["75-99%"] += 1
                    else:
                        bins["100%"] += 1

                return {
                    "title": "توزیع پیشرفت خطوط",
                    "type": "bar",
                    "data": {
                        "labels": list(bins.keys()),
                        "datasets": [{"label": "تعداد خطوط", "data": list(bins.values())}]
                    }
                }

            # گزارش دوم: مصرف متریال بر اساس نوع (برای نمودار دایره‌ای)
            elif report_name == 'material_usage_by_type':
                query = session.query(
                    MTOItem.item_type,
                    func.sum(MTOProgress.used_qty).label('total_used')
                ).join(MTOProgress, MTOItem.id == MTOProgress.mto_item_id) \
                    .filter(MTOProgress.project_id == project_id, MTOItem.item_type != None) \
                    .group_by(MTOItem.item_type).order_by(desc('total_used')).limit(10)

                results = query.all()
                return {
                    "title": "۱۰ نوع متریال پر مصرف",
                    "type": "pie",
                    "data": {
                        "labels": [r.item_type for r in results],
                        "datasets": [{"data": [round(r.total_used, 2) for r in results]}]
                    }
                }

            # گزارش سوم: تاریخچه مصرف در طول زمان (برای نمودار خطی)
            elif report_name == 'consumption_over_time':
                # این گزارش سراسری است و به پروژه وابسته نیست
                query = session.query(
                    func.strftime('%Y-%m-%d', SpoolConsumption.timestamp).label('date'),
                    func.count(SpoolConsumption.id).label('consumption_count')
                ).group_by('date').order_by('date')

                results = query.all()
                return {
                    "title": "تعداد آیتم‌های مصرفی از انبار اسپول در طول زمان",
                    "type": "line",
                    "data": {
                        "labels": [r.date for r in results],
                        "datasets": [{"label": "تعداد مصرف", "data": [r.consumption_count for r in results]}]
                    }
                }

            return {"error": "Report name not found"}, 404

        except Exception as e:
            logging.error(f"Error in get_report_analytics: {e}")
            return {"error": str(e)}, 500
        finally:
            session.close()

    # --------------------------------------------------------------------
    # متدهای لازم برای اسپول MIV SPOOL
    # --------------------------------------------------------------------

    def get_mapped_spool_items(self, mto_item_type, p1_bore):
        """
        آیتم‌های اسپول را بر اساس دیکشنری نگاشت برای نوع کامپوننت و سایز (Bore) فیلتر می‌کند.
        تطبیق به‌صورت انعطاف‌پذیر (startswith/contains) انجام می‌شود.
        """
        session = self.get_session()
        try:
            if not mto_item_type:
                return []

            mto_type_upper = str(mto_item_type).upper().strip()
            spool_equivalents = [mto_type_upper]  # همیشه خود آیتم را به عنوان یک احتمال در نظر بگیر

            # 🔹 جستجو در دیکشنری نگاشت برای پیدا کردن معادل‌ها
            for key, aliases in SPOOL_TYPE_MAPPING.items():
                # اگر نوع MTO با یکی از کلیدها یا معادل‌های آن یکی بود
                if mto_type_upper == key or mto_type_upper in aliases:
                    spool_equivalents.extend([key] + list(aliases))
                    break # وقتی گروه درست پیدا شد، از حلقه خارج شو

            # حذف موارد تکراری از لیست معادل‌ها
            spool_equivalents = list(set(spool_equivalents))

            query = session.query(SpoolItem).options(
                joinedload(SpoolItem.spool)
            ).filter(
                # شرط: موجودی بزرگتر از صفر باشد
                (SpoolItem.qty_available > 0.001) | (SpoolItem.length > 0.001),
                # شرط: نوع کامپوننت یکی از موارد موجود در لیست معادل‌ها باشد
                func.upper(SpoolItem.component_type).in_(spool_equivalents)
            )

            # فیلتر بر اساس سایز (Bore) اگر مشخص شده بود
            if p1_bore is not None:
                query = query.filter(SpoolItem.p1_bore == p1_bore)

            return query.all()

        except Exception as e:
            logging.error(f"Error fetching mapped spool items: {e}")
            return []
        finally:
            session.close()

    def register_spool_consumption(self, miv_record_id, spool_consumptions, user="system"):
        """
        لیستی از مصرف‌های اسپول را برای یک MIV مشخص در دیتابیس ثبت می‌کند.
        spool_consumptions: list of dicts -> [{'spool_item_id': id, 'used_qty': qty}]
        """
        session = self.get_session()
        try:
            miv_record = session.query(MIVRecord).get(miv_record_id)
            if not miv_record:
                return False, "رکورد MIV یافت نشد."

            spool_ids_used = set()

            for consumption in spool_consumptions:
                spool_item_id = consumption['spool_item_id']
                used_qty = consumption['used_qty']

                spool_item = session.get(SpoolItem, spool_item_id)
                if not spool_item:
                    raise Exception(f"آیتم اسپول با شناسه {spool_item_id} یافت نشد.")

                if spool_item.qty_available < used_qty:
                    raise Exception(f"موجودی آیتم {spool_item.id} کافی نیست.")

                # ۱. کاهش موجودی از آیتم اسپول
                spool_item.qty_available -= used_qty

                # ۲. ثبت رکورد مصرف در جدول SpoolConsumption
                new_consumption = SpoolConsumption(
                    spool_item_id=spool_item.id,
                    spool_id=spool_item.spool.id,
                    miv_record_id=miv_record_id,
                    used_qty=used_qty,
                    timestamp=datetime.now()
                )
                session.add(new_consumption)
                spool_ids_used.add(str(spool_item.spool.id))

            session.commit()

            self.log_activity(
                user=user,
                action="REGISTER_SPOOL_CONSUMPTION",
                details=f"Spool items consumed for MIV ID {miv_record_id} from Spools: {', '.join(spool_ids_used)}"
            )
            return True, "مصرف اسپول با موفقیت ثبت شد."
        except Exception as e:
            session.rollback()
            logging.error(f"Error in register_spool_consumption: {e}")
            return False, f"خطا در ثبت مصرف اسپول: {e}"
        finally:
            session.close()

    def get_spool_consumptions_for_miv(self, miv_record_id):
        """
        تمام مصرف‌های اسپول ثبت‌شده برای یک MIV خاص را برمی‌گرداند.
        """
        session = self.get_session()
        try:
            return session.query(SpoolConsumption).filter(
                SpoolConsumption.miv_record_id == miv_record_id
            ).options(joinedload(SpoolConsumption.spool_item)).all()
        finally:
            session.close()

    def _get_matching_mto_progress_for_spool(self, session, spool_item, project_id, line_no):
        """
        یک تابع کمکی برای پیدا کردن رکورد MTOProgress متناظر با یک آیتم اسپول.
        تطبیق بر اساس Type و Bore انجام می‌شود.
        """
        mto_item_type = spool_item.component_type
        p1_bore = spool_item.p1_bore

        # پیدا کردن معادل‌های نوع کامپوننت
        mto_type_upper = str(mto_item_type).upper().strip()
        spool_equivalents = [mto_type_upper]
        for key, aliases in SPOOL_TYPE_MAPPING.items():
            if mto_type_upper == key or mto_type_upper in aliases:
                spool_equivalents.extend([key] + list(aliases))
                break
        spool_equivalents = list(set(spool_equivalents))

        # جستجو برای پیدا کردن MTOItem متناظر
        mto_item_query = session.query(MTOItem).filter(
            MTOItem.project_id == project_id,
            MTOItem.line_no == line_no,
            func.upper(MTOItem.item_type).in_(spool_equivalents)
        )
        if p1_bore is not None:
            mto_item_query = mto_item_query.filter(MTOItem.p1_bore_in == p1_bore)

        mto_item = mto_item_query.first()

        if mto_item:
            # پیدا کردن رکورد MTOProgress مربوط به آن MTOItem
            return session.query(MTOProgress).filter(MTOProgress.mto_item_id == mto_item.id).first()

        return None


    # --------------------------------------------------------------------
    # متدهای لازم برای  مدیریت اسپول ها
    # --------------------------------------------------------------------

    def create_spool(self, spool_data: dict, items_data: list[dict]) -> Tuple[bool, str]:
        """
        یک اسپول جدید همراه با آیتم‌هایش ایجاد می‌کند.
        - عملکرد: صحیح. ابتدا وجود اسپول تکراری را چک می‌کند، سپس اسپول و آیتم‌هایش را می‌سازد.
        """
        session = self.get_session()
        try:
            # بررسی تکراری نبودن شناسه اسپول
            existing_spool = session.query(Spool.id).filter(Spool.spool_id == spool_data["spool_id"]).first()
            if existing_spool:
                return False, f"اسپولی با شناسه '{spool_data['spool_id']}' از قبل وجود دارد."

            new_spool = Spool(
                spool_id=spool_data["spool_id"],
                location=spool_data.get("location"),
                # فیلدهای دیگر در صورت نیاز می‌توانند اضافه شوند
            )
            session.add(new_spool)
            session.flush()  # برای گرفتن ID اسپول جدید

            for item in items_data:
                new_item = SpoolItem(
                    spool_id_fk=new_spool.id,
                    component_type=item.get("component_type"),
                    class_angle=item.get("class_angle"),
                    p1_bore=item.get("p1_bore"),
                    p2_bore=item.get("p2_bore"),
                    material=item.get("material"),
                    schedule=item.get("schedule"),
                    length=item.get("length"),
                    qty_available=item.get("qty_available"),
                    item_code=item.get("item_code")
                )
                session.add(new_item)

            session.commit()
            return True, f"اسپول '{new_spool.spool_id}' با موفقیت ساخته شد."
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در ساخت اسپول: {e}")
            return False, f"خطا در ساخت اسپول: {e}"
        finally:
            session.close()

    def update_spool(self, spool_id: str, updated_data: dict, items_data: list[dict]) -> Tuple[bool, str]:
        """
        اطلاعات یک اسپول و تمام آیتم‌های آن را به‌روزرسانی می‌کند.
        - عملکرد: صحیح. اسپول را پیدا کرده، فیلدهایش را آپدیت می‌کند، آیتم‌های قدیمی را کاملاً پاک کرده و آیتم‌های جدید را جایگزین می‌کند.
        """
        session = self.get_session()
        try:
            spool = session.query(Spool).filter(Spool.spool_id == spool_id).first()
            if not spool:
                return False, "اسپول یافت نشد."

            # آپدیت فیلدهای اصلی اسپول (مانند لوکیشن)
            for key, value in updated_data.items():
                if hasattr(spool, key):
                    setattr(spool, key, value)

            # پاک کردن آیتم‌های قدیمی
            session.query(SpoolItem).filter(SpoolItem.spool_id_fk == spool.id).delete()
            session.flush()

            # افزودن آیتم‌های جدید
            for item in items_data:
                new_item = SpoolItem(
                    spool_id_fk=spool.id,
                    component_type=item.get("component_type"),
                    class_angle=item.get("class_angle"),
                    p1_bore=item.get("p1_bore"),
                    p2_bore=item.get("p2_bore"),
                    material=item.get("material"),
                    schedule=item.get("schedule"),
                    length=item.get("length"),
                    qty_available=item.get("qty_available"),
                    item_code=item.get("item_code")
                )
                session.add(new_item)

            session.commit()
            return True, f"اسپول '{spool_id}' با موفقیت ویرایش شد."
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در آپدیت اسپول: {e}")
            return False, f"خطا در آپدیت اسپول: {e}"
        finally:
            session.close()

    def generate_next_spool_id(self) -> str:
        """
        یک شناسه جدید برای اسپول بر اساس آخرین شناسه موجود تولید می‌کند.
        - عملکرد: صحیح و قوی. با استفاده از regex عدد را از انتهای آخرین شناسه استخراج و یکی به آن اضافه می‌کند.
        """
        session = self.get_session()
        try:
            # برای پیدا کردن آخرین رکورد، بر اساس ID که همیشه صعودی است مرتب می‌کنیم
            last_spool = session.query(Spool).order_by(Spool.id.desc()).first()
            if not last_spool:
                return "S001"

            last_id = last_spool.spool_id
            # پیدا کردن تمام اعداد در شناسه
            numeric_parts = re.findall(r'\d+', last_id)

            if numeric_parts:
                # آخرین عدد پیدا شده را یکی اضافه کن
                next_num = int(numeric_parts[-1]) + 1
            else:
                # اگر هیچ عددی در شناسه نبود، از ID خود رکورد استفاده کن
                next_num = last_spool.id + 1

            # فرمت‌دهی به صورت سه رقمی (e.g., S001, S012, S123)
            return f"S{next_num:03d}"
        except Exception as e:
            logging.error(f"Error generating next spool ID: {e}")
            return f"S_ERR_{datetime.now().microsecond}"
        finally:
            session.close()

    def get_spool_by_id(self, spool_id: str):
        """
        یک اسپول را به همراه تمام آیتم‌های مرتبط با آن برمی‌گرداند.
        - عملکرد: صحیح. استفاده از joinedload باعث می‌شود آیتم‌ها همراه با خود اسپول در یک کوئری خوانده شوند که بهینه است.
        """
        session = self.get_session()
        try:
            spool = session.query(Spool).filter(Spool.spool_id == spool_id).options(joinedload(Spool.items)).first()
            return spool
        finally:
            session.close()  # بستن سشن در هر صورت ضروری است

    def export_spool_data_to_excel(self, file_path: str) -> Tuple[bool, str]:
        """
        داده‌های جداول Spool, SpoolItem و SpoolConsumption را به شیت‌های مجزا در یک فایل اکسل خروجی می‌دهد.
        - عملکرد: صحیح. از کتابخانه پانداز برای خواندن مستقیم از کوئری و نوشتن در اکسل استفاده می‌کند که کارآمد است.
        """
        session = self.get_session()
        try:
            tables_to_export = {
                "Spools": Spool,
                "SpoolItems": SpoolItem,
                "SpoolConsumptions": SpoolConsumption
            }
            with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                for sheet_name, model_class in tables_to_export.items():
                    query = session.query(model_class)
                    df = pd.read_sql(query.statement, session.bind)
                    df.to_excel(writer, sheet_name=sheet_name, index=False)

            self.log_activity("system", "EXPORT_TO_EXCEL", f"Spool data exported to {file_path}")
            return True, f"داده‌ها با موفقیت در فایل {file_path} ذخیره شدند."
        except Exception as e:
            logging.error(f"خطا در خروجی گرفتن اکسل: {e}")
            return False, f"خطا در ایجاد فایل اکسل: {e}"
        finally:
            session.close()

    def get_all_spool_ids(self) -> list[str]:
        """
        لیستی از تمام شناسه‌های اسپول موجود را برمی‌گرداند.
        - عملکرد: صحیح. کوئری ساده برای گرفتن تمام شناسه‌ها و تبدیل نتیجه به لیست رشته‌ها.
        """
        session = self.get_session()
        try:
            results = session.query(Spool.spool_id).order_by(Spool.spool_id).all()
            return [item[0] for item in results]
        except Exception as e:
            logging.error(f"Error fetching all spool IDs: {e}")
            return []
        finally:
            session.close()

    # --------------------------------------------------------------------
    # متدهای لازم برای اپدیت CSV
    # --------------------------------------------------------------------

    def get_or_create_project(self, session, project_name: str) -> Project:
        """یک پروژه را با نام آن پیدا کرده یا در صورت عدم وجود، آن را ایجاد می‌کند."""
        project = session.query(Project).filter(Project.name == project_name).first()
        if not project:
            self.log_activity("system", "CREATE_PROJECT",
                              f"پروژه '{project_name}' به صورت خودکار حین آپدیت داده ساخته شد.", session)
            project = Project(name=project_name)
            session.add(project)
            session.flush()  # برای گرفتن شناسه پروژه جدید
        return project

    def update_project_mto_from_csv(self, project_name: str, mto_file_path: str) -> Tuple[bool, str]:
        """
        --- CHANGE: استفاده از تابع نرمال‌سازی جدید برای انعطاف‌پذیری بیشتر ---
        """
        # --- CHANGE: تعریف نگاشت بر اساس نام‌های نرمال‌شده و ستون‌های ضروری دیتابیس ---
        REQUIRED_DB_COLS = {'line_no', 'description'}
        MTO_COLUMN_MAP = {
            'UNIT': 'unit', 'LINENO': 'line_no', 'CLASS': 'item_class', 'TYPE': 'item_type',
            'DESCRIPTION': 'description', 'ITEMCODE': 'item_code', 'MAT': 'material_code',
            'P1BOREIN': 'p1_bore_in', 'P2BOREIN': 'p2_bore_in', 'P3BOREIN': 'p3_bore_in',
            'LENGTHM': 'length_m', 'QUANTITY': 'quantity', 'JOINT': 'joint', 'INCHDIA': 'inch_dia'
        }

        session = self.get_session()
        try:
            with session.begin():
                self.log_activity("system", "MTO_UPDATE_START", f"شروع آپدیت MTO برای پروژه '{project_name}'.", session)

                project = self.get_or_create_project(session, project_name)
                project_id = project.id

                # خواندن و پردازش DataFrame با تابع جدید
                mto_df_raw = pd.read_csv(mto_file_path, dtype=str).fillna('')
                mto_df = self._normalize_and_rename_df(
                    mto_df_raw, MTO_COLUMN_MAP, REQUIRED_DB_COLS, os.path.basename(mto_file_path)
                )
                mto_df['project_id'] = project_id

                # تبدیل ستون‌های عددی
                for col in ['p1_bore_in', 'p2_bore_in', 'p3_bore_in', 'length_m', 'quantity', 'joint', 'inch_dia']:
                     if col in mto_df.columns:
                        mto_df[col] = pd.to_numeric(mto_df[col], errors='coerce')


                # حذف داده‌های قدیمی (بدون تغییر)
                mto_item_ids_to_delete = session.query(MTOItem.id).filter(MTOItem.project_id == project_id).scalar_subquery()
                session.query(MTOConsumption).filter(MTOConsumption.mto_item_id.in_(mto_item_ids_to_delete)).delete(synchronize_session=False)
                session.query(MTOProgress).filter(MTOProgress.project_id == project_id).delete(synchronize_session=False)
                session.query(MTOItem).filter(MTOItem.project_id == project_id).delete(synchronize_session=False)
                session.flush()

                # درج داده‌های جدید
                mto_records = mto_df.to_dict(orient='records')
                if mto_records:
                    session.bulk_insert_mappings(MTOItem, mto_records)

            self.log_activity("system", "MTO_UPDATE_SUCCESS", f"{len(mto_df)} آیتم MTO برای '{project_name}' آپدیت شد.")
            return True, f"✔ داده‌های MTO برای پروژه '{project_name}' با موفقیت به‌روزرسانی شدند."

        except (ValueError, KeyError, FileNotFoundError) as e:
            session.rollback()
            logging.error(f"شکست در آپدیت MTO برای {project_name}: {e}")
            return False, f"خطا در فایل MTO پروژه '{project_name}': {e}"
        except Exception as e:
            session.rollback()
            logging.error(f"An unexpected error occurred during MTO update for {project_name}: {e}")
            return False, f"خطای غیرمنتظره در آپدیت MTO پروژه '{project_name}': {e}"
        finally:
            session.close()

    def process_selected_csv_files(self, file_paths: List[str]) -> Tuple[bool, str]:
        """
        --- NEW: تابع جدید برای پردازش هوشمند فایل‌های CSV انتخاب شده ---
        فایل‌ها را دسته‌بندی کرده و عملیات آپدیت مربوطه (MTO یا Spool) را اجرا می‌کند.
        """
        try:
            # دسته‌بندی فایل‌های انتخاب شده
            mto_files = {}
            spool_file = None
            spool_items_file = None

            for path in file_paths:
                filename = os.path.basename(path)
                if filename.upper().startswith("MTO-") and filename.upper().endswith(".CSV"):
                    project_name = filename.replace("MTO-", "").replace(".csv", "")
                    mto_files[project_name] = path
                elif filename.upper() == "SPOOLS.CSV":
                    spool_file = path
                elif filename.upper() == "SPOOLITEMS.CSV":
                    spool_items_file = path

            # بررسی شرایط لازم برای هر نوع آپدیت
            can_update_spool = spool_file and spool_items_file
            can_update_mto = bool(mto_files)

            if not can_update_spool and not can_update_mto:
                return False, "هیچ فایل معتبری انتخاب نشد.\nبرای آپدیت MTO، نام فایل باید `MTO-ProjectName.csv` باشد.\nبرای آپدیت Spool، هر دو فایل `Spools.csv` و `SpoolItems.csv` باید انتخاب شوند."

            summary_log = []

            # ۱. آپدیت داده‌های Spool (اگر هر دو فایل انتخاب شده باشند)
            # این عملیات اول انجام می‌شود چون ممکن است MTO به آن وابسته باشد.
            if can_update_spool:
                logging.info("Processing Spool files...")
                success, message = self.replace_all_spool_data(spool_file, spool_items_file)
                if not success:
                    # اگر آپدیت اسپول شکست بخورد، کل عملیات متوقف می‌شود
                    return False, f"خطا در به‌روزرسانی Spool: {message}"
                summary_log.append(message)

            # ۲. آپدیت داده‌های MTO (برای هر فایل MTO به صورت مجزا)
            if can_update_mto:
                for project_name, mto_path in sorted(mto_files.items()):
                    logging.info(f"Processing MTO file for project '{project_name}'...")
                    success, message = self.update_project_mto_from_csv(project_name, mto_path)
                    if not success:
                        # اگر آپدیت یک پروژه شکست بخورد، کل عملیات متوقف می‌شود
                        error_msg = f"خطا در آپدیت پروژه '{project_name}': {message}. عملیات متوقف شد."
                        return False, error_msg
                    summary_log.append(message)

            return True, "\n".join(summary_log)

        except Exception as e:
            import traceback
            logging.error(f"An unexpected error occurred in process_selected_csv_files: {traceback.format_exc()}")
            return False, f"یک خطای پیش‌بینی نشده در پردازش فایل‌ها رخ داد: {e}"

    def replace_all_spool_data(self, spool_file_path: str, spool_items_file_path: str) -> Tuple[bool, str]:
        """
        --- CHANGE: استفاده از تابع نرمال‌سازی جدید برای انعطاف‌پذیری بیشتر ---
        """
        # --- CHANGE: تعریف نگاشت بر اساس نام‌های نرمال‌شده و ستون‌های ضروری دیتابیس ---
        REQUIRED_SPOOL_DB_COLS = {"spool_id"}
        REQUIRED_SPOOL_ITEM_DB_COLS = {"spool_id_str", "component_type"}

        SPOOL_COLUMN_MAP = {
            "SPOOLID": "spool_id", "ROWNO": "row_no", "LOCATION": "location", "COMMAND": "command"
        }
        SPOOL_ITEM_COLUMN_MAP = {
            "SPOOLID": "spool_id_str", "COMPONENTTYPE": "component_type", "CLASSANGLE": "class_angle",
            "P1BORE": "p1_bore", "P2BORE": "p2_bore", "MATERIAL": "material", "SCHEDULE": "schedule",
            "THICKNESS": "thickness", "LENGTH": "length", "QTYAVAILABLE": "qty_available", "ITEMCODE": "item_code"
        }
        session = self.get_session()
        try:
            with session.begin():
                # پردازش فایل Spools.csv
                spools_df_raw = pd.read_csv(spool_file_path, dtype=str).fillna('')
                spools_df = self._normalize_and_rename_df(
                    spools_df_raw, SPOOL_COLUMN_MAP, REQUIRED_SPOOL_DB_COLS, os.path.basename(spool_file_path)
                )
                spools_df['spool_id'] = spools_df['spool_id'].str.strip().str.upper()

                # پردازش فایل SpoolItems.csv
                spool_items_df_raw = pd.read_csv(spool_items_file_path, dtype=str).fillna('')
                spool_items_df = self._normalize_and_rename_df(
                    spool_items_df_raw, SPOOL_ITEM_COLUMN_MAP, REQUIRED_SPOOL_ITEM_DB_COLS, os.path.basename(spool_items_file_path)
                )
                spool_items_df['spool_id_str'] = spool_items_df['spool_id_str'].str.strip().str.upper()

                # حذف داده‌های قدیمی (بدون تغییر)
                session.query(SpoolConsumption).delete(synchronize_session=False)
                session.query(SpoolItem).delete(synchronize_session=False)
                session.query(Spool).delete(synchronize_session=False)
                session.flush()

                # درج داده‌های جدید Spools و ساخت نگاشت (بدون تغییر)
                spool_records = spools_df.to_dict(orient="records")
                if spool_records:
                    session.bulk_insert_mappings(Spool, spool_records)
                session.flush()

                spool_id_map = {spool.spool_id: spool.id for spool in session.query(Spool.id, Spool.spool_id).all()}

                # درج داده‌های جدید SpoolItems (بدون تغییر)
                spool_items_df["spool_id_fk"] = spool_items_df["spool_id_str"].map(spool_id_map)
                spool_items_df.dropna(subset=["spool_id_fk"], inplace=True)
                spool_items_df["spool_id_fk"] = spool_items_df["spool_id_fk"].astype(int)

                # تبدیل ستون‌های عددی
                for col in ["class_angle", "p1_bore", "p2_bore", "thickness", "length", "qty_available"]:
                     if col in spool_items_df.columns:
                        spool_items_df[col] = pd.to_numeric(spool_items_df[col], errors='coerce')

                item_records = spool_items_df.drop(columns=["spool_id_str"]).to_dict(orient="records")
                if item_records:
                    session.bulk_insert_mappings(SpoolItem, item_records)

            self.log_activity("system", "SPOOL_UPDATE_SUCCESS", f"{len(spools_df)} اسپول و {len(spool_items_df)} آیتم اسپول جایگزین شدند.")
            return True, "✔ داده‌های Spool با موفقیت به صورت کامل جایگزین شدند."

        except (ValueError, KeyError, FileNotFoundError) as e:
            return False, f"خطا در فایل‌های Spool: {e}"
        except Exception as e:
            return False, f"خطای دیتابیس در جایگزینی Spool: {e}. (ممکن است رکوردهای مصرفی مانع حذف شده باشند)"
        finally:
            session.close()

    def _validate_and_normalize_df(self, df: pd.DataFrame, required_columns: set, file_name: str) -> pd.DataFrame:
        """
        ستون‌های DataFrame را به حروف بزرگ تبدیل کرده و وجود ستون‌های ضروری را بررسی می‌کند.
        """
        # ۱. تمام ستون‌ها را به حروف بزرگ تبدیل کرده و فضاهای خالی احتمالی را حذف می‌کنیم
        original_columns = df.columns
        df.columns = [str(col).strip().upper() for col in original_columns]

        # ۲. بررسی می‌کنیم که آیا تمام ستون‌های ضروری وجود دارند یا خیر
        present_columns = set(df.columns)
        missing_columns = required_columns - present_columns

        # ۳. اگر ستونی وجود نداشت، یک پیام خطای دقیق نمایش می‌دهیم
        if missing_columns:
            missing_str = ", ".join(sorted(list(missing_columns)))
            raise ValueError(f"فایل '{file_name}' ستون‌های ضروری زیر را ندارد: {missing_str}")

        return df

    def _normalize_and_rename_df(self, df: pd.DataFrame, column_map: dict, required_db_cols: set,
                                 file_name: str) -> pd.DataFrame:
        """
        --- CHANGE: اضافه کردن .copy() در انتها برای جلوگیری از هشدار ---
        1. نام ستون‌های فایل CSV را نرمال‌سازی می‌کند (حذف علائم، تبدیل به حروف بزرگ).
        2. آن‌ها را با دیکشنری column_map به نام‌های ستون دیتابیس تبدیل می‌کند.
        3. وجود ستون‌های ضروری را پس از تغییر نام، بررسی می‌کند.
        """

        def normalize_header(name: str) -> str:
            """یک نام ستون را گرفته و آن را پاکسازی می‌کند."""
            return re.sub(r'[^A-Z0-9]', '', str(name).upper())

        rename_map = {}
        found_db_cols = set()

        for original_col in df.columns:
            normalized_col = normalize_header(original_col)
            if normalized_col in column_map:
                db_col = column_map[normalized_col]
                rename_map[original_col] = db_col
                found_db_cols.add(db_col)

        missing_cols = required_db_cols - found_db_cols
        if missing_cols:
            missing_str = ", ".join(sorted(list(missing_cols)))
            raise ValueError(f"فایل '{file_name}' ستون‌های ضروری زیر را ندارد: {missing_str}")

        df.rename(columns=rename_map, inplace=True)

        final_cols = [col for col in df.columns if col in found_db_cols]

        # --- CHANGE: با افزودن .copy()، یک DataFrame جدید و مستقل ساخته می‌شود ---
        return df[final_cols].copy()

    # --------------------------------------------------------------------
    # متدهای لازم برای جستجوی ایزو ها
    # --------------------------------------------------------------------

    def _normalize_line_key(self, text: str) -> str:
        """(بدون تغییر) کلید نرمال‌شده برای جستجو را تولید می‌کند."""
        if not text: return ""
        return re.sub(r'[^A-Z0-9]+', '', text.upper())

    def _extract_prefix_key(self, text: str) -> str:
        """(بدون تغییر) کلید پیشوند (تا اولین ۶ رقم) را برای جستجو استخراج می‌کند."""
        norm = self._normalize_line_key(text)
        m = re.search(r'(\d{6})', norm)
        return norm[:m.end(1)] if m else norm

    def find_iso_files(self, line_text: str, limit: int = 200) -> list[str]:
        """
        (نسخه هوشمند با مقایسه شباهت)
        ابتدا با یک کوئری سریع کاندیداها را پیدا کرده، سپس آن‌ها را بر اساس بیشترین شباهت
        به ورودی کاربر مرتب می‌کند تا بهترین نتایج در ابتدا نمایش داده شوند.
        """
        from models import IsoFileIndex
        session = self.get_session()
        try:
            norm_input = self._normalize_line_key(line_text)
            if not norm_input:
                return []

            # مرحله ۱: واکشی کاندیداها از دیتابیس با یک کوئری منعطف‌تر
            # ما به جای prefix، از خود norm_input برای جستجوی انعطاف‌پذیرتر استفاده می‌کنیم.
            # این کار نتایج مرتبط بیشتری را در مرحله اول برمی‌گرداند.
            search_term = f"%{norm_input}%"
            candidate_records = session.query(
                IsoFileIndex.file_path,
                IsoFileIndex.normalized_name
            ).filter(
                IsoFileIndex.normalized_name.like(search_term)
            ).limit(limit * 2).all()  # کمی بیشتر از حد مجاز می‌خوانیم تا فضای کافی برای مرتب‌سازی داشته باشیم

            if not candidate_records:
                return []

            # مرحله ۲: محاسبه شباهت و مرتب‌سازی در پایتون
            scored_results = []
            for file_path, normalized_name in candidate_records:
                # SequenceMatcher شباهت بین دو رشته را محاسبه می‌کند
                ratio = difflib.SequenceMatcher(None, norm_input, normalized_name).ratio()
                # اگر ورودی کاربر در نام فایل وجود داشته باشد، یک امتیاز اضافه می‌دهیم
                if norm_input in normalized_name:
                    ratio += 0.1

                scored_results.append((ratio, file_path))

            # مرتب‌سازی نتایج بر اساس امتیاز شباهت (از بیشترین به کمترین)
            scored_results.sort(key=lambda x: x[0], reverse=True)

            # برگرداندن مسیر فایل‌ها به تعداد limit
            return [file_path for ratio, file_path in scored_results[:limit]]

        except Exception as e:
            logging.error(f"خطا در جستجوی هوشمند فایل ISO: {e}")
            return []
        finally:
            session.close()

    def upsert_iso_index_entry(self, file_path: str):
        """یک فایل را در جدول ایندکس درج یا به‌روزرسانی می‌کند (UPSERT)."""
        session = self.get_session()
        try:
            if not os.path.exists(file_path):
                self.remove_iso_index_entry(file_path)
                return

            filename = os.path.basename(file_path)
            normalized_name = self._normalize_line_key(filename)
            prefix_key = self._extract_prefix_key(filename)
            last_modified = datetime.fromtimestamp(os.path.getmtime(file_path))

            # ابتدا سعی کن رکورد موجود را پیدا کنی
            record = session.query(IsoFileIndex).filter(IsoFileIndex.file_path == file_path).first()
            if record:
                # اگر وجود داشت، آپدیت کن
                record.normalized_name = normalized_name
                record.prefix_key = prefix_key
                record.last_modified = last_modified
            else:
                # اگر وجود نداشت، یکی جدید بساز
                record = IsoFileIndex(
                    file_path=file_path,
                    normalized_name=normalized_name,
                    prefix_key=prefix_key,
                    last_modified=last_modified
                )
                session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در upsert_iso_index_entry برای فایل {file_path}: {e}")
        finally:
            session.close()

    def remove_iso_index_entry(self, file_path: str):
        """یک فایل را از جدول ایندکس حذف می‌کند."""
        session = self.get_session()
        try:
            session.query(IsoFileIndex).filter(IsoFileIndex.file_path == file_path).delete()
            session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در remove_iso_index_entry برای فایل {file_path}: {e}")
        finally:
            session.close()

    def rebuild_iso_index_from_scratch(self, base_dir: str, event_handler=None):
        """
        نسخه اصلاح‌شده: بازسازی ایندکس ایزو با batch insert/update/delete
        """
        session = self.get_session()

        def emit_status(message, level):
            if event_handler and hasattr(event_handler, 'status_updated'):
                event_handler.status_updated.emit(message, level)
            else:
                logging.info(f"[{level.upper()}] {message}")

        def emit_progress(value, text=""):
            if event_handler and hasattr(event_handler, 'progress_updated'):
                event_handler.progress_updated.emit(value, text)

        try:
            # --- قدم 1: واکشی ایندکس موجود ---
            emit_status("Loading existing index from database...", "info")
            emit_progress(0, "Loading DB...")

            db_records = session.query(IsoFileIndex.id, IsoFileIndex.file_path, IsoFileIndex.last_modified).all()
            db_files_map = {path: (rec_id, last_mod) for rec_id, path, last_mod in db_records}

            paths_to_add = []
            paths_to_update = []

            # --- قدم 2: شمارش فایل‌ها ---
            emit_status("Counting total files for progress calculation...", "info")
            total_files = 0
            for _, _, files in os.walk(base_dir):
                total_files += len([f for f in files if f.lower().endswith(('.pdf', '.dwg'))])

            if total_files == 0:
                emit_status("No files found. Index will be cleared.", "warning")
                if db_files_map:
                    session.query(IsoFileIndex).delete()
                    session.commit()
                emit_progress(100, "Completed")
                return

            # --- قدم 3: اسکن دیسک ---
            emit_status(f"Scanning {total_files} files on disk...", "info")
            processed_files = 0

            for root, _, files in os.walk(base_dir):
                for filename in files:
                    if not filename.lower().endswith(('.pdf', '.dwg')):
                        continue

                    file_path = os.path.join(root, filename)
                    try:
                        disk_last_modified = datetime.fromtimestamp(os.path.getmtime(file_path))
                    except (FileNotFoundError, OSError):
                        continue

                    if file_path in db_files_map:
                        rec_id, db_last_modified = db_files_map[file_path]
                        if disk_last_modified != db_last_modified:
                            paths_to_update.append({
                                "id": rec_id,  # 👈 کلید اصلی برای update
                                "last_modified": disk_last_modified
                            })
                        del db_files_map[file_path]
                    else:
                        paths_to_add.append({
                            "file_path": file_path,
                            "normalized_name": self._normalize_line_key(filename),
                            "prefix_key": self._extract_prefix_key(filename),
                            "last_modified": disk_last_modified
                        })

                    processed_files += 1
                    if processed_files % 100 == 0:
                        progress = int((processed_files / total_files) * 100)
                        emit_progress(progress, "Scanning...")

            # --- قدم 4: حذف فایل‌های پاک‌شده ---
            paths_to_delete = list(db_files_map.keys())

            # --- قدم 5: اعمال تغییرات ---
            emit_status("Applying changes to the database...", "info")
            emit_progress(95, "Saving...")

            batch_size = 500
            # حذف گروهی
            if paths_to_delete:
                for i in range(0, len(paths_to_delete), batch_size):
                    session.query(IsoFileIndex).filter(
                        IsoFileIndex.file_path.in_(paths_to_delete[i:i + batch_size])
                    ).delete(synchronize_session=False)
                    session.commit()

            # افزودن گروهی
            if paths_to_add:
                for i in range(0, len(paths_to_add), batch_size):
                    session.bulk_insert_mappings(IsoFileIndex, paths_to_add[i:i + batch_size])
                    session.commit()

            # آپدیت گروهی
            if paths_to_update:
                for i in range(0, len(paths_to_update), batch_size):
                    session.bulk_update_mappings(IsoFileIndex, paths_to_update[i:i + batch_size])
                    session.commit()

            emit_status("Index synchronized successfully.", "success")
            emit_progress(100, "Completed!")

        except Exception as e:
            emit_status(f"A critical error occurred during indexing: {e}", "error")
            session.rollback()
        finally:
            session.close()

    def upsert_iso_index_entry(self, file_path: str):
        """
        نسخه نهایی و یکدست‌شده:
        درج یا به‌روزرسانی رکورد ایزو بر اساس مسیر فایل
        """
        session = self.get_session()
        try:
            if not os.path.exists(file_path):
                # اگر فایل وجود ندارد، از ایندکس حذفش کن
                self.remove_iso_index_entry(file_path)
                return

            filename = os.path.basename(file_path)
            normalized_name = self._normalize_line_key(filename)
            prefix_key = self._extract_prefix_key(filename)
            last_modified = datetime.fromtimestamp(os.path.getmtime(file_path))

            record = session.query(IsoFileIndex).filter_by(file_path=file_path).first()
            if record:
                record.normalized_name = normalized_name
                record.prefix_key = prefix_key
                record.last_modified = last_modified
            else:
                record = IsoFileIndex(
                    file_path=file_path,
                    normalized_name=normalized_name,
                    prefix_key=prefix_key,
                    last_modified=last_modified
                )
                session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در upsert_iso_index_entry برای فایل {file_path}: {e}")
        finally:
            session.close()

    def remove_iso_index_entry(self, file_path: str):
        """
        حذف یک فایل از جدول ایندکس
        """
        session = self.get_session()
        try:
            session.query(IsoFileIndex).filter_by(file_path=file_path).delete()
            session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در remove_iso_index_entry برای فایل {file_path}: {e}")
        finally:
            session.close()

 # --------------------------------------------------------------------
    # --- : متدهای اصلی برای خروجی گرفتن (اکسل و PDF) ---
    # --------------------------------------------------------------------

    def export_data_to_file(self, data: List[Dict[str, Any]], file_path: str, report_title: str) -> Tuple[bool, str]:
        """
        یک تابع عمومی که لیستی از دیکشنری‌ها را به فایل اکسل یا PDF صادر می‌کند.
        --- CHANGE: بخش PDF بهینه‌سازی شده برای شکستن متن و تنظیم عرض ستون‌ها ---
        """
        if not data:
            return False, "داده‌ای برای خروجی گرفتن وجود ندارد."

        file_ext = os.path.splitext(file_path)[1].lower()
        df = pd.DataFrame(data)

        try:
            if file_ext == '.xlsx':
                df.to_excel(file_path, index=False, engine='openpyxl')

            elif file_ext == '.pdf':
                # --- بخش بهینه‌سازی شده برای ساخت PDF ---
                from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
                from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                from reportlab.lib import colors
                from reportlab.lib.pagesizes import A4, landscape
                from reportlab.pdfbase.ttfonts import TTFont
                from reportlab.pdfbase import pdfmetrics
                from reportlab.lib.enums import TA_CENTER, TA_LEFT

                # ثبت فونت فارسی
                font_path = "Vazirmatn-Regular.ttf"
                font_name = 'Helvetica'  # فونت پیش‌فرض در صورت نبودن وزیر
                if os.path.exists(font_path):
                    pdfmetrics.registerFont(TTFont('Vazir', font_path))
                    font_name = 'Vazir'
                else:
                    logging.warning(f"Font '{font_path}' not found. Using default Helvetica.")

                # تنظیمات سند با مارجین‌های مناسب
                doc = SimpleDocTemplate(file_path, pagesize=landscape(A4),
                                        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
                elements = []

                # استایل عنوان
                title_style = ParagraphStyle(name='TitleStyle', fontName=font_name, fontSize=16, alignment=TA_CENTER)
                elements.append(Paragraph(report_title, title_style))
                elements.append(Spacer(1, 20))

                # --- ۱. تبدیل تمام سلول‌ها به Paragraph برای شکستن متن ---
                # استایل برای سلول‌های جدول
                cell_style = ParagraphStyle(name='CellStyle', fontName=font_name, fontSize=9, wordWrap='CJK',
                                            alignment=TA_LEFT)
                header_style = ParagraphStyle(name='HeaderStyle', fontName=font_name, fontSize=10, alignment=TA_CENTER)

                # تبدیل DataFrame به لیستی از Paragraph ها
                table_data = []
                # هدر جدول
                headers = [Paragraph(f"<b>{col}</b>", header_style) for col in df.columns]
                table_data.append(headers)
                # ردیف‌های داده
                for index, row in df.iterrows():
                    row_data = [Paragraph(str(item) if item is not None else "", cell_style) for item in row]
                    table_data.append(row_data)

                # --- ۲. محاسبه عرض ستون‌ها به صورت مساوی ---
                available_width = doc.width
                num_columns = len(df.columns)
                col_widths = [available_width / num_columns] * num_columns

                # ساخت جدول با داده‌های جدید و عرض ستون‌های مشخص
                table = Table(table_data, colWidths=col_widths)

                # استایل‌دهی به جدول
                style = TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.darkslategray),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # چینش عمودی در وسط
                    ('FONTNAME', (0, 0), (-1, 0), font_name),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.lavender),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ])
                table.setStyle(style)
                elements.append(table)

                doc.build(elements)
            else:
                return False, f"پسوند فایل '{file_ext}' پشتیبانی نمی‌شود. لطفاً از .xlsx یا .pdf استفاده کنید."

            return True, f"گزارش با موفقیت در مسیر زیر ذخیره شد:\n{file_path}"

        except Exception as e:
            import traceback
            logging.error(f"خطا در ایجاد فایل خروجی: {e}\n{traceback.format_exc()}")
            return False, f"خطا در ساخت فایل خروجی: {e}"

    def export_miv_records_to_file(self, records: List[MIVRecord], file_path: str) -> Tuple[bool, str]:
        """
        لیستی از رکوردهای MIV را به فرمت دلخواه (اکسل/PDF) خروجی می‌دهد.
        """
        report_title = f"MIV Records Report for Line: {records[0].line_no if records else 'N/A'}"
        data_to_export = [
            {
                "ID": r.id,
                "MIV Tag": r.miv_tag,
                "Location": r.location,
                "Status": r.status,
                "Comment": r.comment,
                "Registered For": r.registered_for,
                "Registered By": r.registered_by,
                "Last Updated": r.last_updated.strftime('%Y-%m-%d %H:%M') if r.last_updated else ""
            } for r in records
        ]
        return self.export_data_to_file(data_to_export, file_path, report_title)

    def export_detailed_line_report_to_file(self, project_id: int, line_no: str, file_path: str) -> Tuple[bool, str]:
        """
        گزارش دقیقی از وضعیت متریال یک خط خاص را تولید کرده و در فایل اکسل یا PDF ذخیره می‌کند.
        """
        # گام ۱: دریافت داده‌های غنی‌شده از تابع موجود
        # آرگومان readonly=False تضمین می‌کند که آمار خط قبل از گزارش‌گیری به‌روز است.
        line_data = self.get_enriched_line_progress(project_id, line_no, readonly=False)

        if not line_data:
            return False, f"هیچ داده‌ای برای خط '{line_no}' جهت گزارش‌گیری یافت نشد."

        # گام ۲: آماده‌سازی عنوان گزارش
        report_title = f"Detailed Material Status for Line: {line_no}"

        # گام ۳: استفاده از تابع عمومی برای ساخت فایل خروجی
        # این تابع به صورت خودکار اکسل یا PDF را بر اساس پسوند فایل تشخیص می‌دهد.
        return self.export_data_to_file(line_data, file_path, report_title)

    def _export_to_excel(self, df: pd.DataFrame, file_path: str, report_title: str) -> Tuple[bool, str]:
        """
        داده‌ها را به یک فایل اکسل با عرض ستون خودکار و هدر استایل‌دار صادر می‌کند.
        """
        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            sheet_name = report_title[:30].replace('/', '-') # نام شیت نمی‌تواند شامل برخی کاراکترها باشد
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]

            # استایل‌دهی به هدر
            header_font = pd.io.excel._openpyxl_writer.Font(bold=True, color='FFFFFF')
            header_fill = pd.io.excel._openpyxl_writer.PatternFill(start_color='4F81BD', end_color='4F81BD', fill_type='solid')

            for col_num, value in enumerate(df.columns.values, 1):
                cell = worksheet.cell(row=1, column=col_num)
                cell.font = header_font
                cell.fill = header_fill

            # تنظیم خودکار عرض ستون‌ها
            for column_cells in worksheet.columns:
                max_length = 0
                column_letter = column_cells[0].column_letter
                for cell in column_cells:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = (max_length + 2)
                worksheet.column_dimensions[column_letter].width = adjusted_width

        return True, f"Excel report saved successfully to:\n{file_path}"
