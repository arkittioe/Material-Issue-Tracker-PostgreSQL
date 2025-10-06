# file: data/spool_service.py

import os
import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import func, desc
from sqlalchemy.orm import joinedload

from data.db_session import DBSessionManager
from data.constants import SPOOL_TYPE_MAPPING
from models import (
    Spool, SpoolItem, SpoolConsumption, MIVRecord,
    MTOItem, MTOProgress
)

class SpoolService:
    def __init__(self, session_getter=DBSessionManager.get_session, activity_logger: Optional[Any] = None):
        """
        سرویس مدیریت عملیات اسپول‌ها
        :param session_getter: تابعی که Session جدید می‌سازد
        :param activity_logger: تابع ثبت فعالیت‌ها (user, action, details)
        """
        self._session_getter = session_getter
        self.log_activity = activity_logger or (lambda **kwargs: None)

    # --------------------------------------------------------------------
    # گزارش‌ها
    # --------------------------------------------------------------------
    def get_spool_inventory_report(self, **filters) -> Dict[str, Any]:
        session = self._session_getter()
        try:
            query = session.query(Spool, SpoolItem).join(
                SpoolItem, Spool.id == SpoolItem.spool_id_fk
            ).filter(
                (SpoolItem.qty_available > 0.001) | (SpoolItem.length > 0.001)
            )

            # فیلترها
            if filters.get('spool_id'):
                query = query.filter(Spool.spool_id.ilike(f"%{filters['spool_id']}%"))
            if filters.get('location'):
                query = query.filter(Spool.location.ilike(f"%{filters['location']}%"))
            if filters.get('component_type'):
                query = query.filter(SpoolItem.component_type.ilike(f"%{filters['component_type']}%"))
            if filters.get('material'):
                query = query.filter(SpoolItem.material.ilike(f"%{filters['material']}%"))

            # مرتب‌سازی
            sort_by = filters.get('sort_by', 'spool_id')
            sort_order = filters.get('sort_order', 'asc')
            sort_column = getattr(Spool, sort_by, getattr(SpoolItem, sort_by, Spool.spool_id))
            query = query.order_by(desc(sort_column) if sort_order == 'desc' else sort_column)

            # صفحه‌بندی
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
        session = self._session_getter()
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

    # --------------------------------------------------------------------
    # جستجو و نگاشت
    # --------------------------------------------------------------------
    def get_mapped_spool_items(self, mto_item_type, p1_bore):
        session = self._session_getter()
        try:
            if not mto_item_type:
                return []
            mto_type_upper = str(mto_item_type).upper().strip()
            spool_equivalents = [mto_type_upper]
            for key, aliases in SPOOL_TYPE_MAPPING.items():
                if mto_type_upper == key or mto_type_upper in aliases:
                    spool_equivalents.extend([key] + list(aliases))
                    break
            spool_equivalents = list(set(spool_equivalents))

            query = session.query(SpoolItem).options(joinedload(SpoolItem.spool)).filter(
                (SpoolItem.qty_available > 0.001) | (SpoolItem.length > 0.001),
                func.upper(SpoolItem.component_type).in_(spool_equivalents)
            )
            if p1_bore is not None:
                query = query.filter(SpoolItem.p1_bore == p1_bore)
            return query.all()
        except Exception as e:
            logging.error(f"Error fetching mapped spool items: {e}")
            return []
        finally:
            session.close()

    # --------------------------------------------------------------------
    # مصرف و ارتباط با MIV
    # --------------------------------------------------------------------
    def register_spool_consumption(self, miv_record_id, spool_consumptions, user="system"):
        session = self._session_getter()
        try:
            miv_record = session.get(MIVRecord, miv_record_id)
            if not miv_record:
                return False, "رکورد MIV یافت نشد."
            spool_ids_used = set()
            for consumption in spool_consumptions:
                spool_item_id = consumption['spool_item_id']
                used_qty = consumption['used_qty']
                spool_item = session.get(SpoolItem, spool_item_id)
                if not spool_item:
                    raise Exception(f"آیتم اسپول با شناسه {spool_item_id} یافت نشد.")

                is_pipe = "PIPE" in (spool_item.component_type or "").upper()
                if is_pipe:
                    if (spool_item.length or 0) < used_qty:
                        raise Exception(f"موجودی طول کافی نیست برای PIPE اسپول {spool_item.spool.spool_id}.")
                    spool_item.length -= used_qty
                else:
                    if (spool_item.qty_available or 0) < used_qty:
                        raise Exception(f"موجودی آیتم {spool_item.id} کافی نیست.")
                    spool_item.qty_available -= used_qty

                session.add(SpoolConsumption(
                    spool_item_id=spool_item.id,
                    spool_id=spool_item.spool.id,
                    miv_record_id=miv_record_id,
                    used_qty=used_qty,
                    timestamp=datetime.now()
                ))
                spool_ids_used.add(str(spool_item.spool.id))
            session.commit()
            self.log_activity(user=user, action="REGISTER_SPOOL_CONSUMPTION",
                              details=f"Spool items consumed for MIV ID {miv_record_id} from Spools: {', '.join(spool_ids_used)}")
            return True, "مصرف اسپول با موفقیت ثبت شد."
        except Exception as e:
            session.rollback()
            logging.error(f"Error in register_spool_consumption: {e}")
            return False, f"خطا در ثبت مصرف اسپول: {e}"
        finally:
            session.close()

    def get_spool_consumptions_for_miv(self, miv_record_id):
        session = self._session_getter()
        try:
            return session.query(SpoolConsumption).filter(
                SpoolConsumption.miv_record_id == miv_record_id
            ).options(joinedload(SpoolConsumption.spool_item)).all()
        finally:
            session.close()

    def _get_matching_mto_progress_for_spool(self, session, spool_item, project_id, line_no):
        mto_item_type = spool_item.component_type
        p1_bore = spool_item.p1_bore
        mto_type_upper = str(mto_item_type).upper().strip()
        spool_equivalents = [mto_type_upper]
        for key, aliases in SPOOL_TYPE_MAPPING.items():
            if mto_type_upper == key or mto_type_upper in aliases:
                spool_equivalents.extend([key] + list(aliases))
                break
        spool_equivalents = list(set(spool_equivalents))
        mto_item_query = session.query(MTOItem).filter(
            MTOItem.project_id == project_id,
            MTOItem.line_no == line_no,
            func.upper(MTOItem.item_type).in_(spool_equivalents)
        )
        if p1_bore is not None:
            mto_item_query = mto_item_query.filter(MTOItem.p1_bore_in == p1_bore)
        mto_item = mto_item_query.first()
        if mto_item:
            return session.query(MTOProgress).filter(MTOProgress.mto_item_id == mto_item.id).first()
        return None

    # --------------------------------------------------------------------
    # CRUD اسپول
    # --------------------------------------------------------------------
    def create_spool(self, spool_data: dict, items_data: List[dict]) -> Tuple[bool, str]:
        session = self._session_getter()
        try:
            existing_spool = session.query(Spool.id).filter(Spool.spool_id == spool_data["spool_id"]).first()
            if existing_spool:
                return False, f"اسپولی با شناسه '{spool_data['spool_id']}' از قبل وجود دارد."
            new_spool = Spool(spool_id=spool_data["spool_id"], location=spool_data.get("location"))
            session.add(new_spool)
            session.flush()
            for item in items_data:
                session.add(SpoolItem(spool_id_fk=new_spool.id, **item))
            session.commit()
            return True, f"اسپول '{new_spool.spool_id}' با موفقیت ساخته شد."
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در ساخت اسپول: {e}")
            return False, f"خطا در ساخت اسپول: {e}"
        finally:
            session.close()

    def update_spool(self, spool_id: str, updated_data: dict, items_data: List[dict]) -> Tuple[bool, str]:
        session = self._session_getter()
        try:
            spool = session.query(Spool).filter(Spool.spool_id == spool_id).first()
            if not spool:
                return False, "اسپول یافت نشد."
            for key, value in updated_data.items():
                if hasattr(spool, key):
                    setattr(spool, key, value)
            session.query(SpoolItem).filter(SpoolItem.spool_id_fk == spool.id).delete()
            session.flush()
            for item in items_data:
                session.add(SpoolItem(spool_id_fk=spool.id, **item))
            session.commit()
            return True, f"اسپول '{spool_id}' با موفقیت ویرایش شد."
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در آپدیت اسپول: {e}")
            return False, f"خطا در آپدیت اسپول: {e}"
        finally:
            session.close()

    def generate_next_spool_id(self) -> str:
        session = self._session_getter()
        try:
            last_spool = session.query(Spool).order_by(Spool.id.desc()).first()
            if not last_spool:
                return "S001"
            last_id = last_spool.spool_id
            numeric_parts = re.findall(r'\d+', last_id)
            next_num = int(numeric_parts[-1]) + 1 if numeric_parts else last_spool.id + 1
            return f"S{next_num:03d}"
        except Exception as e:
            logging.error(f"Error generating next spool ID: {e}")
            return f"S_ERR_{datetime.now().microsecond}"
        finally:
            session.close()

    def get_spool_by_id(self, spool_id: str):
        session = self._session_getter()
        try:
            return session.query(Spool).filter(Spool.spool_id == spool_id).options(joinedload(Spool.items)).first()
        finally:
            session.close()

    def export_spool_data_to_excel(self, file_path: str) -> Tuple[bool, str]:
        session = self._session_getter()
        try:
            tables_to_export = {
                "Spools": Spool,
                "SpoolItems": SpoolItem,
                "SpoolConsumptions": SpoolConsumption
            }
            with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                for sheet_name, model_class in tables_to_export.items():
                    df = pd.read_sql(session.query(model_class).statement, session.bind)
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
            self.log_activity("system", "EXPORT_TO_EXCEL", f"Spool data exported to {file_path}")
            return True, f"داده‌ها با موفقیت در فایل {file_path} ذخیره شدند."
        except Exception as e:
            logging.error(f"خطا در خروجی گرفتن اکسل: {e}")
            return False, f"خطا در ایجاد فایل اکسل: {e}"
        finally:
            session.close()

    def get_all_spool_ids(self) -> List[str]:
        session = self._session_getter()
        try:
            return [item[0] for item in session.query(Spool.spool_id).order_by(Spool.spool_id).all()]
        except Exception as e:
            logging.error(f"Error fetching all spool IDs: {e}")
            return []
        finally:
            session.close()

    # --------------------------------------------------------------------
    # جایگزینی کامل داده از CSV
    # --------------------------------------------------------------------
    def replace_all_spool_data(self, spool_file_path: str, spool_items_file_path: str) -> Tuple[bool, str]:
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
        session = self._session_getter()
        try:
            with session.begin():
                spools_df_raw = pd.read_csv(spool_file_path, dtype=str).fillna('')
                spools_df = self._normalize_and_rename_df(spools_df_raw, SPOOL_COLUMN_MAP,
                                                          REQUIRED_SPOOL_DB_COLS, os.path.basename(spool_file_path))
                spools_df['spool_id'] = spools_df['spool_id'].str.strip().str.upper()

                spool_items_df_raw = pd.read_csv(spool_items_file_path, dtype=str).fillna('')
                spool_items_df = self._normalize_and_rename_df(spool_items_df_raw, SPOOL_ITEM_COLUMN_MAP,
                                                               REQUIRED_SPOOL_ITEM_DB_COLS,
                                                               os.path.basename(spool_items_file_path))
                spool_items_df['spool_id_str'] = spool_items_df['spool_id_str'].str.strip().str.upper()

                session.query(SpoolConsumption).delete(synchronize_session=False)
                session.query(SpoolItem).delete(synchronize_session=False)
                session.query(Spool).delete(synchronize_session=False)
                session.flush()

                spool_records = spools_df.to_dict(orient="records")
                if spool_records:
                    session.bulk_insert_mappings(Spool, spool_records)
                session.flush()
                spool_id_map = {spool.spool_id: spool.id for spool in session.query(Spool.id, Spool.spool_id).all()}

                spool_items_df["spool_id_fk"] = spool_items_df["spool_id_str"].map(spool_id_map)
                spool_items_df.dropna(subset=["spool_id_fk"], inplace=True)
                spool_items_df["spool_id_fk"] = spool_items_df["spool_id_fk"].astype(int)
                for col in ["class_angle", "p1_bore", "p2_bore", "thickness", "length", "qty_available"]:
                    if col in spool_items_df.columns:
                        spool_items_df[col] = pd.to_numeric(spool_items_df[col], errors='coerce')
                item_records = spool_items_df.drop(columns=["spool_id_str"]).to_dict(orient="records")
                if item_records:
                    session.bulk_insert_mappings(SpoolItem, item_records)

            self.log_activity("system", "SPOOL_UPDATE_SUCCESS",
                              f"{len(spools_df)} اسپول و {len(spool_items_df)} آیتم اسپول جایگزین شدند.")
            return True, "✔ داده‌های Spool با موفقیت به صورت کامل جایگزین شدند."
        except (ValueError, KeyError, FileNotFoundError) as e:
            return False, f"خطا در فایل‌های Spool: {e}"
        except Exception as e:
            return False, f"خطای دیتابیس در جایگزینی Spool: {e}. (ممکن است رکوردهای مصرفی مانع حذف شده باشند)"
        finally:
            session.close()

    def _normalize_and_rename_df(self, df, column_map, required_cols, filename):
        df.columns = [c.strip().upper().replace(" ", "") for c in df.columns]
        if not required_cols.issubset(set(column_map.values())):
            raise ValueError(f"ستون‌های اجباری موجود نیستند در فایل {filename}")
        rename_map = {k: v for k, v in column_map.items() if k in df.columns}
        return df.rename(columns=rename_map)
