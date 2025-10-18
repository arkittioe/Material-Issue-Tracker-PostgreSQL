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
    SpoolItem, MTOItem, Project,InventoryItem,
    Warehouse, InventoryTransaction, InventoryItem,
    InventoryTransaction, MaterialReservation
)
from data.consumption_service import ConsumptionService

class MIVService:
    def __init__(
            self,
            session_factory,
            activity_logger: Optional[Callable[[str, str, str], None]] = None,
            line_progress_rebuilder: Optional[Callable[[int, str], None]] = None
    ):
        """
        Initialize MIVService

        :param session_factory: تابع برای ایجاد Session جدید
        :param activity_logger: تابعی با امضا (user, action, details) برای ثبت لاگ
        :param line_progress_rebuilder: تابعی با امضا (project_id, line_no) برای بازسازی MTO Progress
        """
        self.session_factory = session_factory
        self.log_activity = activity_logger or self._default_logger
        self.rebuild_mto_progress_for_line = line_progress_rebuilder

        # اضافه کردن ConsumptionService برای مدیریت یکپارچه مصرف
        from data.consumption_service import ConsumptionService
        from data.warehouse_service import WarehouseService

        # ایجاد WarehouseService برای استفاده مشترک
        self.warehouse_service = WarehouseService(session_factory, activity_logger)

        # ایجاد ConsumptionService با استفاده از warehouse_service
        self.consumption_service = ConsumptionService(
            session_factory=session_factory,
            activity_logger=activity_logger,
            warehouse_service=self.warehouse_service
        )

        # Logger برای debugging
        self.logger = logging.getLogger(__name__)

    def _default_logger(self, user: str, action: str, details: str = ""):
        """لاگر پیش‌فرض در صورت عدم ارائه activity_logger"""
        print(f"[{datetime.now()}] User: {user}, Action: {action}, Details: {details}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def register_miv_record(
            self,
            project_id: int,
            form_data: Dict[str, Any],
            consumption_items: List[Dict[str, Any]],
            spool_consumption_items: Optional[List[Dict[str, Any]]] = None,
            warehouse_consumption_items: Optional[List[Dict[str, Any]]] = None
    ) -> tuple[bool, str]:
        """
        ثبت رکورد MIV جدید با مدیریت یکپارچه مصرف از انبار

        Args:
            project_id: شناسه پروژه
            form_data: اطلاعات فرم MIV
            consumption_items: آیتم‌های مصرفی MTO
            spool_consumption_items: آیتم‌های مصرفی اسپول
            warehouse_consumption_items: آیتم‌های مصرفی انبار

        Returns:
            (success: bool, message: str)
        """
        session: Session = self.session_factory()
        shortages_info = []  # لیست کسری‌ها برای اطلاع‌رسانی

        try:
            # ایجاد رکورد MIV جدید
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
            session.flush()  # برای دریافت ID

            # ثبت مصرف MTO (آیتم‌های معمولی بدون انبار)
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

                    # بررسی موجودی اسپول
                    if is_pipe:
                        if (spool_item.length or 0) < used_qty:
                            raise ValueError(f"Insufficient length for pipe in spool {spool_item.spool.spool_id}.")
                        spool_item.length -= used_qty
                    else:
                        if (spool_item.qty_available or 0) < used_qty:
                            raise ValueError(
                                f"Insufficient qty for {spool_item.component_type} in spool {spool_item.spool.spool_id}.")
                        spool_item.qty_available -= used_qty

                    # ثبت مصرف اسپول
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

            # 🔥 ثبت مصرف از انبار عمومی با استفاده از ConsumptionService
            if warehouse_consumption_items:
                # آماده‌سازی داده‌ها برای ConsumptionService
                consumption_data = []
                feedback_data = []

                for item in warehouse_consumption_items:
                    # اضافه کردن به لیست مصرف
                    consumption_data.append({
                        'inventory_item_id': item.get('inventory_item_id'),
                        'quantity': item['used_qty'],
                        'mto_item_id': item.get('mto_item_id')
                    })

                    # آماده‌سازی داده‌های feedback
                    if item.get('mto_item_id') and item.get('inventory_item_id'):
                        mto_item = session.get(MTOItem, item['mto_item_id'])
                        inv_item = session.get(InventoryItem, item['inventory_item_id'])

                        if mto_item and inv_item:
                            feedback_data.append({
                                'mto_code': mto_item.item_code,
                                'warehouse_code': inv_item.item_code,
                                'confidence': item.get('confidence', 0.95),
                                'match_source': item.get('match_source', 'MIV_SELECTION')
                            })

                # ثبت feedback برای هر انتخاب
                for feedback in feedback_data:
                    try:
                        self.consumption_service.record_user_feedback(
                            mto_code=feedback['mto_code'],
                            warehouse_code=feedback['warehouse_code'],
                            confidence=feedback['confidence'],
                            user=form_data['Registered By'],
                            source=feedback['match_source'],
                            project_id=project_id
                        )
                        self.logger.info(f"Feedback recorded: {feedback['mto_code']} -> {feedback['warehouse_code']}")
                    except Exception as e:
                        self.logger.warning(f"Failed to record feedback: {e}")

                # پردازش مصرف با ConsumptionService
                consumption_result = self.consumption_service.process_miv_consumption(
                    miv_id=new_record.id,
                    items=consumption_data,
                    user=form_data['Registered By'],
                    allow_partial=True  # اجازه مصرف جزئی در صورت کسری
                )

                # بررسی نتیجه
                if not consumption_result['success']:
                    # در صورت خطای کامل، rollback
                    raise ValueError(
                        f"Error in consumption processing: {consumption_result.get('error', 'Unknown error')}")

                # مدیریت کسری‌ها
                if consumption_result.get('shortages'):
                    for shortage in consumption_result['shortages']:
                        shortages_info.append({
                            'item_code': shortage.get('item_code'),
                            'shortage_qty': shortage.get('shortage_qty'),
                            'unit': shortage.get('unit'),
                            'message': shortage.get('message')
                        })

                    # اضافه کردن اطلاعات کسری به comment
                    shortage_notes = [s['message'] for s in shortages_info]
                    new_record.comment = (new_record.comment or "") + " | کسری‌ها: " + ", ".join(shortage_notes)

                # به‌روزرسانی وضعیت MIV بر اساس نتیجه
                if consumption_result.get('partial'):
                    new_record.status = 'PARTIALLY_ISSUED'
                else:
                    new_record.status = 'ISSUED'

            # Commit تمام تغییرات
            session.commit()

            # بازسازی MTO Progress
            if self.rebuild_mto_progress_for_line:
                try:
                    self.rebuild_mto_progress_for_line(project_id, form_data['Line No'])
                except Exception as e:
                    self.logger.error(f"Error rebuilding MTO progress: {e}")

            # ثبت در Activity Log
            if self.log_activity:
                details = f"MIV Tag '{form_data['MIV Tag']}' for Line '{form_data['Line No']}'"
                if shortages_info:
                    details += f" - {len(shortages_info)} shortage(s) recorded"

                self.log_activity(
                    user=form_data['Registered By'],
                    action="REGISTER_MIV",
                    details=details
                )

            # تحلیل و یادگیری از الگوها (در background)
            try:
                # بررسی الگوهای جدید برای یادگیری خودکار
                patterns = self.consumption_service.analyze_feedback_patterns()
                if patterns:
                    # فعالسازی خودکار mapping‌های پرتکرار
                    self.consumption_service.auto_learn_from_patterns(dry_run=False)
                    self.logger.info(f"Auto-learning completed with {len(patterns)} patterns")
            except Exception as e:
                self.logger.warning(f"Auto-learning failed: {e}")

            # پیام نهایی
            success_message = "رکورد با موفقیت ثبت شد."
            if shortages_info:
                shortage_summary = f"\n⚠️ تعداد {len(shortages_info)} کسری ثبت شد:"
                for s in shortages_info[:3]:  # نمایش حداکثر 3 کسری
                    shortage_summary += f"\n- {s['item_code']}: {s['shortage_qty']} {s['unit']}"
                if len(shortages_info) > 3:
                    shortage_summary += f"\n... و {len(shortages_info) - 3} مورد دیگر"
                success_message += shortage_summary

            return True, success_message

        except Exception as e:
            # Rollback در صورت خطا
            session.rollback()
            import traceback
            error_details = traceback.format_exc()
            self.logger.error(f"خطا در ثبت رکورد MIV: {e}\n{error_details}")

            # ثبت خطا در Activity Log
            if self.log_activity:
                self.log_activity(
                    user=form_data.get('Registered By', 'system'),
                    action="REGISTER_MIV_FAILED",
                    details=f"Failed to register MIV '{form_data.get('MIV Tag', 'Unknown')}': {str(e)}"
                )

            return False, f"خطا در ثبت رکورد: {e}"

        finally:
            session.close()

    def update_miv_items(
            self,
            miv_record_id: int,
            updated_items: List[Dict[str, Any]],
            updated_spool_items: List[Dict[str, Any]],
            updated_warehouse_items: List[Dict[str, Any]] = None,  # 🆕 اضافه شد
            user: str = "system"
    ) -> tuple[bool, str]:
        session: Session = self.session_factory()
        try:
            record = session.get(MIVRecord, miv_record_id)
            if not record:
                return False, f"MIV با شناسه {miv_record_id} یافت نشد."

            project_id = record.project_id
            line_no = record.line_no

            # بازگشت موجودی قبلی انبار عمومی 🆕
            old_warehouse_consumptions = session.query(MTOConsumption).filter(
                MTOConsumption.miv_record_id == miv_record_id,
                MTOConsumption.inventory_item_id.isnot(None)
            ).all()

            for old_c in old_warehouse_consumptions:
                inv_item = session.get(InventoryItem, old_c.inventory_item_id)
                if inv_item:
                    # بازگشت موجودی
                    inv_item.available_qty += old_c.used_qty

                    # ثبت تراکنش بازگشت
                    session.add(InventoryTransaction(
                        warehouse_id=inv_item.warehouse_id,
                        inventory_item_id=inv_item.id,
                        transaction_type="RETURN",
                        quantity=old_c.used_qty,
                        balance_before=inv_item.available_qty - old_c.used_qty,
                        balance_after=inv_item.available_qty,
                        reference_type="MIV_UPDATE",
                        reference_id=miv_record_id,
                        performed_by=user,
                        remarks=f"بازگشت موجودی برای ویرایش MIV {miv_record_id}"
                    ))

            # بازگشت موجودی قبلی اسپول (کد موجود)
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

            # ثبت مصرف‌های جدید از انبار 🆕
            if updated_warehouse_items:
                for item in updated_warehouse_items:
                    session.add(MTOConsumption(
                        mto_item_id=item['mto_item_id'],
                        miv_record_id=miv_record_id,
                        inventory_item_id=item.get('inventory_item_id'),
                        used_qty=item['used_qty'],
                        timestamp=datetime.now()
                    ))

                    # کاهش موجودی انبار
                    if item.get('inventory_item_id'):
                        inv_item = session.get(InventoryItem, item['inventory_item_id'])
                        if inv_item:
                            if inv_item.available_qty < item['used_qty']:
                                raise ValueError(f"موجودی کافی نیست برای {inv_item.item_code}")

                            inv_item.available_qty -= item['used_qty']
                            if inv_item.reserved_qty and inv_item.reserved_qty > 0:
                                inv_item.reserved_qty = max(0, inv_item.reserved_qty - item['used_qty'])

                            # ثبت تراکنش
                            session.add(InventoryTransaction(
                                warehouse_id=inv_item.warehouse_id,
                                inventory_item_id=inv_item.id,
                                transaction_type="OUT",
                                quantity=item['used_qty'],
                                balance_before=inv_item.available_qty + item['used_qty'],
                                balance_after=inv_item.available_qty,
                                reference_type="MIV_UPDATE",
                                reference_id=miv_record_id,
                                performed_by=user,
                                remarks=f"مصرف برای MIV {miv_record_id}"
                            ))

            # ثبت مصرف‌های جدید اسپول (کد موجود)
            # ... (کد موجود برای اسپول‌ها)

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
