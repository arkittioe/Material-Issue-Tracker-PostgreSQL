# file: data/data_manager_facade.py
"""
Facade اصلی برای ماژول data
- نگهداری تمام سرویس‌ها با یک SessionFactory مشترک
- فوروارد کردن متدهای DataManager مونولیت به سرویس ماژولار شده
"""

import os
from typing import Optional, Any
from urllib.parse import quote_plus
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from config_manager import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME
from models import Base

# Services
from data.db_session import DBSessionManager
from data.constants import *
from data.activity_service import ActivityService
from data.miv_service import MIVService
from data.project_service import ProjectService
from data.mto_service import MTOService
from data.csv_service import CSVService
from data.spool_service import SpoolService
from data.report_service import ReportService
from data.iso_service import ISOService
from data.warehouse_service import WarehouseService


class DataManagerFacade:
    """
    نسخه ماژولار شده DataManager
    """

    def __init__(self, db_user: Optional[str] = None, db_password: Optional[str] = None):
        # ------- Credential Priority -------
        user = (db_user or os.getenv("APP_DB_USER") or DB_USER or "").strip()
        pwd = (db_password or os.getenv("APP_DB_PASSWORD") or DB_PASSWORD or "").strip()

        # ------- Safe Encode --------
        user_enc = quote_plus(user)
        pwd_enc = quote_plus(pwd)

        db_url = f"postgresql+psycopg2://{user_enc}:{pwd_enc}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

        # ------- Engine & SessionFactory --------
        self.engine = create_engine(
            db_url,
            echo=False,
            pool_size=10,
            max_overflow=20
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

        # ------- Services Instances --------
        self.activity_service = ActivityService(self.session_factory)
        self.project_service = ProjectService(self.session_factory)
        self.mto_service = MTOService(self.session_factory)
        self.spool_service = SpoolService(self.session_factory, self.activity_service.log_activity)
        # ✅ اصلاح امضای CSVService بر اساس csv_service.py
        self.csv_service = CSVService(
            self.activity_service.log_activity,  # activity_logger
            self.project_service.get_or_create_project,  # project_getter
            self.spool_service.replace_all_spool_data,  # spool_replacer
            self.session_factory  # session_getter
        )

        # self.miv_service = MIVService(self.session_factory, self.project_service, self.activity_service)
        self.miv_service = MIVService(
            session_factory=self.session_factory,
            activity_logger=self.activity_service.log_activity,
            line_progress_rebuilder=self.mto_service.rebuild_mto_progress_for_line
        )

        self.report_service = ReportService(
            self.project_service,  # دسترسی به متدهای پروژه
            self.activity_service.log_activity,  # لاگ‌زن اصلی
            self.session_factory  # استفاده از Session مشترک
        )

        self.iso_service = ISOService(self.session_factory)

        # اضافه کردن سرویس انبار
        self.warehouse_service = WarehouseService(
            self.session_factory,
            self.activity_service.log_activity
        )

    # ----------------- DB Utils -----------------
    def get_session(self):
        return self.session_factory()

    @staticmethod
    def test_connection(db_user: str, db_password: str):
        try:
            user_enc = quote_plus(db_user.strip())
            pwd_enc = quote_plus(db_password.strip())
            db_url = f"postgresql+psycopg2://{user_enc}:{pwd_enc}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            test_engine = create_engine(db_url)
            with test_engine.connect() as conn:
                conn.execute(func.now())
            return True, "OK"
        except OperationalError as e:
            return False, f"اتصال ناموفق: {e}"
        except Exception as e:
            return False, f"خطا: {e}"

    # ---------------- ActivityService ----------------
    def log_activity(self, *args, **kwargs): return self.activity_service.log_activity(*args, **kwargs)
    def get_activity_logs(self, *args, **kwargs): return self.activity_service.get_activity_logs(*args, **kwargs)

    # ---------------- ProjectService -----------------
    def get_all_projects(self, *args, **kwargs): return self.project_service.get_all_projects(*args, **kwargs)
    def get_project_by_name(self, *args, **kwargs): return self.project_service.get_project_by_name(*args, **kwargs)

    def get_mto_progress_for_line(self, *args, **kwargs):
        return self.project_service.get_mto_progress_for_line(*args, **kwargs)

    def get_project_progress(self, *args, **kwargs): return self.project_service.get_project_progress(*args, **kwargs)
    def get_line_progress(self, *args, **kwargs): return self.project_service.get_line_progress(*args, **kwargs)
    def generate_project_report(self, *args, **kwargs): return self.project_service.generate_project_report(*args, **kwargs)
    def rename_project(self, *args, **kwargs): return self.project_service.rename_project(*args, **kwargs)
    def copy_line_to_project(self, *args, **kwargs): return self.project_service.copy_line_to_project(*args, **kwargs)
    def check_duplicates_in_project(self, *args, **kwargs): return self.project_service.check_duplicates_in_project(*args, **kwargs)
    def is_line_complete(self, *args, **kwargs): return self.project_service.is_line_complete(*args, **kwargs)
    def get_enriched_line_progress(self, *args, **kwargs): return self.project_service.get_enriched_line_progress(*args, **kwargs)
    def initialize_mto_progress_for_line(self, *args, **kwargs): return self.project_service.initialize_mto_progress_for_line(*args, **kwargs)
    def update_mto_progress(self, *args, **kwargs): return self.project_service.update_mto_progress(*args, **kwargs)
    def get_used_qty(self, *args, **kwargs): return self.project_service.get_used_qty(*args, **kwargs)
    def suggest_line_no(self, *args, **kwargs): return self.project_service.suggest_line_no(*args, **kwargs)
    def get_lines_for_project(self, *args, **kwargs): return self.project_service.get_lines_for_project(*args, **kwargs)
    def get_project_analytics(self, *args, **kwargs): return self.project_service.get_project_analytics(*args, **kwargs)
    def get_project_mto_summary(self, *args, **kwargs): return self.project_service.get_project_mto_summary(*args, **kwargs)
    def get_project_line_status_list(self, *args, **kwargs): return self.project_service.get_project_line_status_list(*args, **kwargs)
    def get_or_create_project(self, *args, **kwargs): return self.project_service.get_or_create_project(*args, **kwargs)

    # ---------------- MTOService ---------------------
    def get_mto_item_by_id(self, *args, **kwargs): return self.mto_service.get_mto_item_by_id(*args, **kwargs)
    def rebuild_mto_progress_for_line(self, *args, **kwargs): return self.mto_service.rebuild_mto_progress_for_line(*args, **kwargs)
    def get_mto_items_for_line(self, *args, **kwargs): return self.mto_service.get_mto_items_for_line(*args, **kwargs)
    def get_data_as_dataframe(self, *args, **kwargs): return self.mto_service.get_data_as_dataframe(*args, **kwargs)
    def backup_database(self, *args, **kwargs): return self.mto_service.backup_database(*args, **kwargs)

    # ---------------- CSVService ---------------------
    def update_project_mto_from_csv(self, *args, **kwargs):
        return self.csv_service.update_project_mto_from_csv(*args, **kwargs)
    def process_selected_csv_files(self, *args, **kwargs):
        return self.csv_service.process_selected_csv_files(*args, **kwargs)
    def _validate_and_normalize_df(self, *args, **kwargs): return self.csv_service._validate_and_normalize_df(*args, **kwargs)
    def _normalize_and_rename_df(self, *args, **kwargs): return self.csv_service._normalize_and_rename_df(*args, **kwargs)
    def _normalize_line_key(self, *args, **kwargs): return self.csv_service._normalize_line_key(*args, **kwargs)

    # ---------------- MIVService ---------------------
    def register_miv_record(self, *args, **kwargs): return self.miv_service.register_miv_record(*args, **kwargs)
    def update_miv_items(self, *args, **kwargs): return self.miv_service.update_miv_items(*args, **kwargs)
    def delete_miv_record(self, *args, **kwargs): return self.miv_service.delete_miv_record(*args, **kwargs)
    def get_consumptions_for_miv(self, *args, **kwargs): return self.miv_service.get_consumptions_for_miv(*args, **kwargs)
    def is_duplicate_miv_tag(self, *args, **kwargs): return self.miv_service.is_duplicate_miv_tag(*args, **kwargs)
    def get_line_no_suggestions(self, *args, **kwargs): return self.miv_service.get_line_no_suggestions(*args, **kwargs)
    def search_miv_by_line_no(self, *args, **kwargs): return self.miv_service.search_miv_by_line_no(*args, **kwargs)
    def get_miv_data(self, *args, **kwargs): return self.miv_service.get_miv_data(*args, **kwargs)

    # ---------------- SpoolService -------------------
    def get_spool_inventory_report(self, *args, **kwargs): return self.spool_service.get_spool_inventory_report(*args, **kwargs)
    def get_spool_consumption_history(self, *args, **kwargs): return self.spool_service.get_spool_consumption_history(*args, **kwargs)
    def get_mapped_spool_items(self, *args, **kwargs): return self.spool_service.get_mapped_spool_items(*args, **kwargs)
    def register_spool_consumption(self, *args, **kwargs): return self.spool_service.register_spool_consumption(*args, **kwargs)
    def get_spool_consumptions_for_miv(self, *args, **kwargs): return self.spool_service.get_spool_consumptions_for_miv(*args, **kwargs)
    def _get_matching_mto_progress_for_spool(self, *args, **kwargs): return self.spool_service._get_matching_mto_progress_for_spool(*args, **kwargs)
    def create_spool(self, *args, **kwargs): return self.spool_service.create_spool(*args, **kwargs)
    def update_spool(self, *args, **kwargs): return self.spool_service.update_spool(*args, **kwargs)
    def generate_next_spool_id(self, *args, **kwargs): return self.spool_service.generate_next_spool_id(*args, **kwargs)
    def get_spool_by_id(self, *args, **kwargs): return self.spool_service.get_spool_by_id(*args, **kwargs)
    def export_spool_data_to_excel(self, *args, **kwargs): return self.spool_service.export_spool_data_to_excel(*args, **kwargs)
    def get_all_spool_ids(self, *args, **kwargs): return self.spool_service.get_all_spool_ids(*args, **kwargs)
    def replace_all_spool_data(self, *args, **kwargs): return self.spool_service.replace_all_spool_data(*args, **kwargs)

    # ---------------- ReportService ------------------
    def get_detailed_line_report(self, *args, **kwargs): return self.report_service.get_detailed_line_report(*args, **kwargs)
    def get_shortage_report(self, *args, **kwargs): return self.report_service.get_shortage_report(*args, **kwargs)
    def get_report_analytics(self, *args, **kwargs): return self.report_service.get_report_analytics(*args, **kwargs)
    def export_data_to_file(self, *args, **kwargs): return self.report_service.export_data_to_file(*args, **kwargs)
    def export_miv_records_to_file(self, *args, **kwargs): return self.report_service.export_miv_records_to_file(*args, **kwargs)
    def export_detailed_line_report_to_file(self, *args, **kwargs): return self.report_service.export_detailed_line_report_to_file(*args, **kwargs)
    def _export_to_excel(self, *args, **kwargs): return self.report_service._export_to_excel(*args, **kwargs)

    # ---------------- ISOService ---------------------
    def find_iso_files(self, *args, **kwargs): return self.iso_service.find_iso_files(*args, **kwargs)
    def upsert_iso_index_entry(self, *args, **kwargs): return self.iso_service.upsert_iso_index_entry(*args, **kwargs)
    def remove_iso_index_entry(self, *args, **kwargs): return self.iso_service.remove_iso_index_entry(*args, **kwargs)
    def rebuild_iso_index_from_scratch(self, *args, **kwargs): return self.iso_service.rebuild_iso_index_from_scratch(*args, **kwargs)
    def _extract_prefix_key(self, *args, **kwargs): return self.iso_service._extract_prefix_key(*args, **kwargs)

    # ---------------- WarehouseService -------------------
    # مدیریت انبار
    def create_warehouse(self, *args, **kwargs):
        return self.warehouse_service.create_warehouse(*args, **kwargs)

    def get_all_warehouses(self, *args, **kwargs):
        return self.warehouse_service.get_all_warehouses(*args, **kwargs)

    def get_warehouse_by_code(self, *args, **kwargs):
        return self.warehouse_service.get_warehouse_by_code(*args, **kwargs)

    def update_warehouse(self, *args, **kwargs):
        return self.warehouse_service.update_warehouse(*args, **kwargs)

    # مدیریت موجودی
    def add_inventory_item(self, *args, **kwargs):
        return self.warehouse_service.add_inventory_item(*args, **kwargs)

    def update_inventory_item(self, *args, **kwargs):
        return self.warehouse_service.update_inventory_item(*args, **kwargs)

    def get_inventory_items(self, *args, **kwargs):
        return self.warehouse_service.get_inventory_items(*args, **kwargs)

    def get_inventory_by_material(self, *args, **kwargs):
        return self.warehouse_service.get_inventory_by_material(*args, **kwargs)

    def check_availability(self, *args, **kwargs):
        return self.warehouse_service.check_availability(*args, **kwargs)

    # رزرو و تراکنش
    def reserve_material(self, *args, **kwargs):
        return self.warehouse_service.reserve_material(*args, **kwargs)

    def cancel_reservation(self, *args, **kwargs):
        return self.warehouse_service.cancel_reservation(*args, **kwargs)

    def consume_reservation(self, *args, **kwargs):
        return self.warehouse_service.consume_reservation(*args, **kwargs)

    def get_active_reservations(self, *args, **kwargs):
        return self.warehouse_service.get_active_reservations(*args, **kwargs)

    # تراکنش‌های انبار
    def record_inventory_in(self, *args, **kwargs):
        return self.warehouse_service.record_inventory_in(*args, **kwargs)

    def record_inventory_out(self, *args, **kwargs):
        return self.warehouse_service.record_inventory_out(*args, **kwargs)

    def adjust_inventory(self, *args, **kwargs):  # ✅ اصلاح شد
        return self.warehouse_service.adjust_inventory(*args, **kwargs)

    def get_transactions_history(self, *args, **kwargs):  # ✅ اصلاح شد
        return self.warehouse_service.get_transactions_history(*args, **kwargs)

    # گزارشات انبار
    def get_inventory_summary(self, *args, **kwargs):  # ✅ اصلاح شد
        return self.warehouse_service.get_inventory_summary(*args, **kwargs)

    def get_stock_movement_report(self, *args, **kwargs):  # ✅ اضافه شد
        return self.warehouse_service.get_stock_movement_report(*args, **kwargs)

    def get_low_stock_items(self, *args, **kwargs):
        return self.warehouse_service.get_low_stock_items(*args, **kwargs)

    def get_inventory_valuation(self, *args, **kwargs):  # ✅ اصلاح شد
        return self.warehouse_service.get_inventory_valuation(*args, **kwargs)

    def transfer_between_warehouses(self, *args, **kwargs):  # ✅ اضافه شد
        return self.warehouse_service.transfer_between_warehouses(*args, **kwargs)
