# file: data/miv_service.py
"""
سرویس مدیریت MIV:
    - ثبت رکوردهای MIV
    - بروزرسانی مصرف آیتم‌ها
    - حذف MIV و بازگردانی موجودی
    - جستجو و پیشنهادها
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    MIVRecord, MTOConsumption, SpoolConsumption,
    SpoolItem, MTOItem, Project
)


class MIVService:
    def __init__(
            self,
            session_factory,  # اضافه کردن session_factory
            activity_logger: Optional[Callable[[str, str, str], None]] = None,
            line_progress_rebuilder: Optional[Callable[[int, str], None]] = None
    ):
        """
        :param session_factory: تابع برای ایجاد Session جدید
        :param activity_logger: تابعی با امضا (user, action, details) برای ثبت لاگ
        :param line_progress_rebuilder: تابعی با امضا (project_id, line_no) برای بازسازی MTO Progress
        """
        self.session_factory = session_factory
        self.log_activity = activity_logger
        self.rebuild_mto_progress_for_line = line_progress_rebuilder

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def register_miv_record(
            self,
            project_id: int,
            form_data: Dict[str, Any],
            consumption_items: List[Dict[str, Any]],
            spool_consumption_items: Optional[List[Dict[str, Any]]] = None
    ) -> tuple[bool, str]:
        session: Session = self.session_factory()  # تغییر
        try:
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

            # ثبت مصرف MTO
            for item in consumption_items:
                session.add(MTOConsumption(
                    mto_item_id=item['mto_item_id'],
                    miv_record_id=new_record.id,
                    used_qty=item['used_qty'],
                    timestamp=datetime.now()
                ))

            # ثبت مصرف اسپول
            if spool_consumption_items:
                spool_notes = []
                for consumption in spool_consumption_items:
                    spool_item = session.get(SpoolItem, consumption['spool_item_id'])
                    if not spool_item:
                        raise ValueError(f"Spool item ID {consumption['spool_item_id']} not found.")

                    used_qty = consumption['used_qty']
                    is_pipe = "PIPE" in (spool_item.component_type or "").upper()

                    if is_pipe:
                        if (spool_item.length or 0) < used_qty:
                            raise ValueError(f"Insufficient length for pipe in spool {spool_item.spool.spool_id}.")
                        spool_item.length -= used_qty
                    else:
                        if (spool_item.qty_available or 0) < used_qty:
                            raise ValueError(
                                f"Insufficient qty for {spool_item.component_type} in spool {spool_item.spool.spool_id}.")
                        spool_item.qty_available -= used_qty

                    session.add(SpoolConsumption(
                        spool_item_id=spool_item.id,
                        spool_id=spool_item.spool.id,
                        miv_record_id=new_record.id,
                        used_qty=used_qty,
                        timestamp=datetime.now()
                    ))

                    unit = "m" if is_pipe else "عدد"
                    spool_notes.append(
                        f"{used_qty:.2f} {unit} از {spool_item.component_type} (اسپول: {spool_item.spool.spool_id})"
                    )

                if spool_notes:
                    new_record.comment = (new_record.comment or "") + " | مصرف اسپول: " + ", ".join(spool_notes)

            session.commit()

            if self.rebuild_mto_progress_for_line:
                self.rebuild_mto_progress_for_line(project_id, form_data['Line No'])

            if self.log_activity:
                self.log_activity(
                    user=form_data['Registered By'],
                    action="REGISTER_MIV",
                    details=f"MIV Tag '{form_data['MIV Tag']}' for Line '{form_data['Line No']}'"
                )
            return True, "رکورد با موفقیت ثبت شد."

        except Exception as e:
            session.rollback()
            import traceback
            logging.error(f"خطا در ثبت رکورد: {e}\n{traceback.format_exc()}")
            return False, f"خطا در ثبت رکورد: {e}"
        finally:
            session.close()

    def update_miv_items(
            self,
            miv_record_id: int,
            updated_items: List[Dict[str, Any]],
            updated_spool_items: List[Dict[str, Any]],
            user: str = "system"
    ) -> tuple[bool, str]:
        session: Session = self.session_factory()  # تغییر
        try:
            record = session.get(MIVRecord, miv_record_id)
            if not record:
                return False, f"MIV با شناسه {miv_record_id} یافت نشد."

            project_id = record.project_id
            line_no = record.line_no

            # بازگشت موجودی قبلی اسپول
            for old_c in session.query(SpoolConsumption).filter(SpoolConsumption.miv_record_id == miv_record_id):
                spool_item = session.get(SpoolItem, old_c.spool_item_id)
                if spool_item:
                    is_pipe = "PIPE" in (spool_item.component_type or "").upper()
                    if is_pipe:
                        spool_item.length = (spool_item.length or 0) + old_c.used_qty
                    else:
                        spool_item.qty_available = (spool_item.qty_available or 0) + old_c.used_qty

            # حذف مصرف‌های قبلی
            session.query(MTOConsumption).filter(MTOConsumption.miv_record_id == miv_record_id).delete()
            session.query(SpoolConsumption).filter(SpoolConsumption.miv_record_id == miv_record_id).delete()
            session.flush()

            # ثبت مصرف‌های جدید MTO
            for item in updated_items:
                session.add(MTOConsumption(
                    mto_item_id=item["mto_item_id"],
                    miv_record_id=miv_record_id,
                    used_qty=item["used_qty"],
                    timestamp=datetime.now()
                ))

            # ثبت مصرف‌های جدید اسپول
            spool_notes = []
            for s_item in updated_spool_items or []:
                spool_item = session.get(SpoolItem, s_item['spool_item_id'])
                if not spool_item:
                    raise ValueError(f"آیتم اسپول با شناسه {s_item['spool_item_id']} یافت نشد.")

                used_qty = s_item['used_qty']
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

                unit = "mm" if is_pipe else "عدد"
                spool_notes.append(
                    f"{used_qty:.1f} {unit} از {spool_item.component_type} (اسپول: {spool_item.spool.spool_id})"
                )

            session.commit()

            if self.rebuild_mto_progress_for_line:
                self.rebuild_mto_progress_for_line(project_id, line_no)

            if self.log_activity:
                self.log_activity(
                    user=user,
                    action="UPDATE_MIV_ITEMS",
                    details=f"Consumption items updated for MIV {miv_record_id}"
                )
            return True, "آیتم‌های مصرفی با موفقیت بروزرسانی شدند."

        except Exception as e:
            session.rollback()
            import traceback
            logging.error(f"خطا در بروزرسانی آیتم‌های MIV {miv_record_id}: {e}\n{traceback.format_exc()}")
            return False, f"خطا در بروزرسانی آیتم‌های MIV: {e}"
        finally:
            session.close()

    def delete_miv_record(self, record_id: int, user: str = "system") -> tuple[bool, str]:
        session: Session = self.session_factory()  # تغییر
        try:
            record = session.get(MIVRecord, record_id)
            if not record:
                return False, "رکورد یافت نشد."

            project_id = record.project_id
            line_no = record.line_no
            miv_tag = record.miv_tag

            # بازگشت موجودی اسپول
            for consumption in session.query(SpoolConsumption).filter(SpoolConsumption.miv_record_id == record_id):
                spool_item = session.get(SpoolItem, consumption.spool_item_id)
                if spool_item:
                    is_pipe = "PIPE" in (spool_item.component_type or "").upper()
                    if is_pipe:
                        spool_item.length = (spool_item.length or 0) + consumption.used_qty
                    else:
                        spool_item.qty_available = (spool_item.qty_available or 0) + consumption.used_qty

            # حذف رکوردهای مصرف
            session.query(MTOConsumption).filter(MTOConsumption.miv_record_id == record_id).delete()
            session.query(SpoolConsumption).filter(SpoolConsumption.miv_record_id == record_id).delete()

            session.delete(record)
            session.commit()

            if self.rebuild_mto_progress_for_line:
                self.rebuild_mto_progress_for_line(project_id, line_no)

            if self.log_activity:
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

    # ------------------------------------------------------------------
    # متدهای کمکی و جستجو
    # ------------------------------------------------------------------
    def get_consumptions_for_miv(self, miv_record_id: int) -> Dict[int, float]:
        session: Session = self.session_factory()  # تغییر
        try:
            consumptions = session.query(MTOConsumption).filter(
                MTOConsumption.miv_record_id == miv_record_id
            ).all()
            return {item.mto_item_id: item.used_qty for item in consumptions}
        except Exception as e:
            logging.error(f"Error fetching consumptions for MIV {miv_record_id}: {e}")
            return {}
        finally:
            session.close()

    def is_duplicate_miv_tag(self, miv_tag: str, project_id: int) -> bool:
        session: Session = self.session_factory()  # تغییر
        try:
            exists = session.query(MIVRecord.id).filter(
                MIVRecord.project_id == project_id,
                MIVRecord.miv_tag == miv_tag
            ).first()
            return exists is not None
        finally:
            session.close()

    def get_line_no_suggestions(self, typed_text: str, top_n: int = 15) -> List[Dict[str, Any]]:
        if not typed_text or len(typed_text) < 2:
            return []
        session: Session = self.session_factory()  # تغییر
        try:
            search_term = f"%{typed_text}%"
            results = (
                session.query(MTOItem.line_no, Project.name, Project.id)
                .join(Project, MTOItem.project_id == Project.id)
                .filter(MTOItem.line_no.ilike(search_term))
                .distinct()
                .limit(top_n)
                .all()
            )
            return [
                {
                    'display': f"{line_no}  ({project_name})",
                    'line_no': line_no,
                    'project_name': project_name,
                    'project_id': project_id
                }
                for line_no, project_name, project_id in results
            ]
        except Exception as e:
            logging.error(f"خطا در پیشنهاد سراسری شماره خط: {e}")
            return []
        finally:
            session.close()

    def search_miv_by_line_no(self, project_id: int, line_no: str) -> List[MIVRecord]:
        session: Session = self.session_factory()  # تغییر
        try:
            return session.query(MIVRecord).filter(
                MIVRecord.project_id == project_id,
                MIVRecord.line_no == line_no
            ).all()
        finally:
            session.close()

    def get_miv_data(self, project_id: int) -> List[MIVRecord]:
        """
        دریافت تمام رکوردهای MIV برای یک پروژه
        """
        session: Session = self.session_factory()  # تغییر
        try:
            return session.query(MIVRecord).filter(
                MIVRecord.project_id == project_id
            ).order_by(MIVRecord.last_updated.desc()).all()
        finally:
            session.close()
