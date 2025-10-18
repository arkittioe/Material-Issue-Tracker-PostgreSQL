# data/consumption_service.py
"""
سرویس مدیریت مصرف، بازخورد و اتوماسیون انبار
Consumption, Feedback & Warehouse Automation Service
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any, Union
from enum import Enum
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from sqlalchemy.exc import IntegrityError

# Import models
from models import (
    Base, Project, MIVRecord, MTOItem, MTOProgress, MTOConsumption,
    Warehouse, InventoryItem, InventoryTransaction, MaterialReservation,
    InventoryAdjustment, ItemMapping, MaterialSearchHistory, MaterialSynonym
)

# Import existing services
from data.warehouse_service import WarehouseService


import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple, Callable

from sqlalchemy import func, Float, or_, and_, desc
from sqlalchemy.orm import Session
from PyQt6.QtWidgets import QMessageBox


class ShortageManagement:
    """کلاس مدیریت کسری‌ها"""
    CRITICAL = "CRITICAL"  # کسری بحرانی
    HIGH = "HIGH"          # کسری با اولویت بالا
    MEDIUM = "MEDIUM"      # کسری متوسط
    LOW = "LOW"            # کسری کم اهمیت


class TransactionStatus(Enum):
    """وضعیت‌های تراکنش"""
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    PARTIAL = "PARTIAL"


class MappingSource(Enum):
    """منابع تطبیق"""
    EXACT_MATCH = "EXACT_MATCH"
    RULE_BASED = "RULE_BASED"
    NLP_MATCH = "NLP_MATCH"
    USER_SELECTION = "USER_SELECTION"
    USER_FEEDBACK = "USER_FEEDBACK"
    SYNONYM_MATCH = "SYNONYM_MATCH"


class ConsumptionService:
    """
    سرویس جامع مدیریت مصرف و بازخورد
    شامل: مصرف از انبار، ثبت بازخورد، یادگیری، rollback
    """

    def __init__(
            self,
            session_factory,
            activity_logger=None,
            warehouse_service: Optional['WarehouseService'] = None
    ):
        """
        Initialize ConsumptionService

        Args:
            session_factory: Factory for creating database sessions
            activity_logger: Optional activity logging function with signature (user, action, details)
            warehouse_service: Optional WarehouseService instance for delegation
        """
        self.session_factory = session_factory
        self.activity_logger = activity_logger or self._default_logger

        # ایجاد یا استفاده از WarehouseService موجود
        if warehouse_service:
            self.warehouse_service = warehouse_service
        else:
            from data.warehouse_service import WarehouseService
            self.warehouse_service = WarehouseService(session_factory, activity_logger)

        # Logger برای debugging
        self.logger = logging.getLogger(__name__)

        # پارامترهای تنظیم (Configuration Parameters)
        self.min_confidence_for_auto_activation = 0.85  # حداقل اطمینان برای فعالسازی خودکار
        self.min_usage_for_auto_activation = 3  # حداقل تعداد استفاده برای فعالسازی خودکار
        self.shortage_high_threshold = 10  # آستانه کسری بالا (برای تعیین اولویت)

        # Cache تنظیمات
        self._cache_ttl_minutes = 30
        self._pattern_cache = {}
        self._last_cache_update = None

        # آمار عملکرد
        self.stats = {
            'total_consumptions': 0,
            'successful_consumptions': 0,
            'failed_consumptions': 0,
            'total_feedbacks': 0,
            'auto_activations': 0,
            'shortages_created': 0
        }

    def _default_logger(self, user: str, action: str, details: str = ""):
        """لاگر پیش‌فرض در صورت عدم ارائه activity_logger"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.logger.info(f"[{timestamp}] User: {user} | Action: {action} | Details: {details}")

    def _default_logger(self, user: str, action: str, details: str = ""):
        """لاگر پیش‌فرض"""
        self.logger.info(f"[{user}] {action}: {details}")

    def _log_activity(self, action: str, details: str = "", user: str = "System"):
        """ثبت لاگ فعالیت"""
        self.activity_logger(user=user, action=action, details=details)

    # ================== بخش 1: ثبت بازخورد و یادگیری ==================

    def record_user_feedback(
            self,
            mto_item: Dict[str, Any],  # اطلاعات آیتم MTO
            selected_warehouse_item_id: int,
            miv_id: int,
            user_id: str,
            match_source: Union[MappingSource, str] = MappingSource.USER_SELECTION,
            confidence_at_selection: float = None,
            project_id: int = None,
            additional_context: Dict = None
    ) -> Tuple[bool, str, Optional[int]]:
        """
        ثبت انتخاب کاربر به عنوان بازخورد برای یادگیری سیستم

        Args:
            mto_item: دیکشنری حاوی اطلاعات آیتم MTO (item_code, description, size, etc.)
            selected_warehouse_item_id: شناسه آیتم انتخاب شده از انبار
            miv_id: شناسه MIV مرتبط
            user_id: شناسه کاربر
            match_source: منبع تطبیق
            confidence_at_selection: میزان اطمینان در لحظه انتخاب
            project_id: شناسه پروژه
            additional_context: اطلاعات اضافی

        Returns:
            (success, message, mapping_id)
        """
        session = self.session_factory()
        try:
            # تبدیل match_source به enum اگر رشته باشد
            if isinstance(match_source, str):
                try:
                    match_source = MappingSource[match_source]
                except KeyError:
                    match_source = MappingSource.USER_SELECTION

            # دریافت اطلاعات آیتم انبار
            warehouse_item = session.query(InventoryItem).get(selected_warehouse_item_id)
            if not warehouse_item:
                return False, f"آیتم انبار با شناسه {selected_warehouse_item_id} یافت نشد", None

            # استخراج اطلاعات MTO
            mto_code = mto_item.get('item_code', '')
            mto_description = mto_item.get('description', '')
            mto_size = mto_item.get('size', '') or self._extract_size_from_description(mto_description)

            # محاسبه confidence score بر اساس نوع منبع
            if confidence_at_selection is None:
                confidence_map = {
                    MappingSource.EXACT_MATCH: 1.0,
                    MappingSource.RULE_BASED: 0.85,
                    MappingSource.NLP_MATCH: 0.75,
                    MappingSource.SYNONYM_MATCH: 0.80,
                    MappingSource.USER_SELECTION: 0.90,
                    MappingSource.USER_FEEDBACK: 0.95
                }
                confidence_at_selection = confidence_map.get(match_source, 0.80)

            # بررسی mapping موجود
            existing_mapping = session.query(ItemMapping).filter(
                and_(
                    ItemMapping.source_code == mto_code,
                    ItemMapping.target_code == warehouse_item.item_code,
                    or_(
                        ItemMapping.source_size == mto_size,
                        ItemMapping.source_size.is_(None)
                    )
                )
            ).first()

            mapping_id = None

            if existing_mapping:
                # به‌روزرسانی mapping موجود
                old_usage = existing_mapping.usage_count
                existing_mapping.usage_count += 1

                # محاسبه میانگین وزنی confidence
                total_confidence = (existing_mapping.confidence_score * old_usage + confidence_at_selection)
                existing_mapping.confidence_score = min(total_confidence / existing_mapping.usage_count, 1.0)

                existing_mapping.last_used = datetime.utcnow()
                existing_mapping.updated_at = datetime.utcnow()

                # بررسی برای فعالسازی خودکار
                if (not existing_mapping.is_active and
                        existing_mapping.usage_count >= self.min_usage_for_auto_activation and
                        existing_mapping.confidence_score >= self.min_confidence_for_auto_activation):
                    existing_mapping.is_active = True
                    existing_mapping.notes = (
                        f"Auto-activated after {existing_mapping.usage_count} uses "
                        f"with confidence {existing_mapping.confidence_score:.2f}"
                    )
                    self._log_activity(
                        action="AUTO_ACTIVATE_MAPPING",
                        details=f"Mapping {mto_code} -> {warehouse_item.item_code} activated",
                        user="System"
                    )

                mapping_id = existing_mapping.id
                message = f"Mapping updated (Usage: {existing_mapping.usage_count})"

            else:
                # ایجاد mapping جدید
                new_mapping = ItemMapping(
                    source_code=mto_code,
                    source_description=mto_description,
                    source_size=mto_size,
                    target_code=warehouse_item.item_code,
                    target_description=warehouse_item.description,
                    target_size=warehouse_item.size,
                    mapping_type='USER_FEEDBACK',
                    confidence_score=confidence_at_selection,
                    usage_count=1,
                    last_used=datetime.utcnow(),
                    is_active=False,  # ابتدا غیرفعال
                    created_by=user_id,
                    mapping_rules={
                        'source': match_source.value,
                        'miv_id': miv_id,
                        'context': additional_context or {}
                    },
                    notes=f"Created from MIV#{miv_id} by {user_id}"
                )
                session.add(new_mapping)
                session.flush()
                mapping_id = new_mapping.id
                message = "New mapping created"

            # ثبت در تاریخچه جستجو
            search_history = MaterialSearchHistory(
                search_term=mto_code,
                search_filters={
                    'description': mto_description,
                    'size': mto_size,
                    'source': match_source.value
                },
                search_context=f'MIV#{miv_id}',
                selected_item_code=warehouse_item.item_code,
                selected_item_description=warehouse_item.description,
                selected_warehouse_id=warehouse_item.warehouse_id,
                user_id=user_id,
                project_id=project_id,
                timestamp=datetime.utcnow(),
                was_successful=True,
                user_feedback='CORRECT'  # فرض بر صحت انتخاب کاربر
            )
            session.add(search_history)

            # بررسی و ثبت synonym اگر کدها متفاوت باشند
            if mto_code != warehouse_item.item_code:
                self._check_and_create_synonym(
                    session,
                    mto_code,
                    warehouse_item.item_code,
                    mto_description,
                    warehouse_item.description,
                    user_id
                )

            session.commit()

            self._log_activity(
                action="FEEDBACK_RECORDED",
                details=f"Mapped {mto_code} to {warehouse_item.item_code} (Source: {match_source.value})",
                user=user_id
            )

            return True, message, mapping_id

        except IntegrityError as e:
            session.rollback()
            self.logger.error(f"Integrity error in feedback recording: {e}")
            return False, "خطا در ذخیره‌سازی: احتمالاً تکراری است", None
        except Exception as e:
            session.rollback()
            self.logger.error(f"Error recording user feedback: {e}")
            return False, f"خطا در ثبت بازخورد: {str(e)}", None
        finally:
            session.close()

    def _extract_size_from_description(self, description: str) -> str:
        """استخراج سایز از توضیحات"""
        if not description:
            return ""

        # Pattern matching for common size formats
        import re
        patterns = [
            r'\d+["\']?\s*x\s*\d+',  # 2x4, 2"x4"
            r'\d+\s*(?:inch|in|mm|cm)',  # 10 inch, 25mm
            r'DN\s*\d+',  # DN100
            r'\d+#',  # 150#
        ]

        for pattern in patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match.group()

        return ""

    def _check_and_create_synonym(
            self,
            session: Session,
            code1: str,
            code2: str,
            desc1: str,
            desc2: str,
            user_id: str
    ):
        """بررسی و ایجاد synonym در صورت نیاز"""
        try:
            # بررسی وجود synonym
            existing = session.query(MaterialSynonym).filter(
                or_(
                    and_(
                        MaterialSynonym.primary_code == code1,
                        MaterialSynonym.synonym_code == code2
                    ),
                    and_(
                        MaterialSynonym.primary_code == code2,
                        MaterialSynonym.synonym_code == code1
                    )
                )
            ).first()

            if not existing:
                synonym = MaterialSynonym(
                    primary_code=code1,
                    primary_description=desc1,
                    synonym_code=code2,
                    synonym_description=desc2,
                    synonym_type='USER_MAPPED',
                    is_verified=False,
                    confidence_score=0.7,
                    source='USER_FEEDBACK',
                    created_by=user_id
                )
                session.add(synonym)

        except Exception as e:
            self.logger.warning(f"Could not create synonym: {e}")

    # ================== بخش 2: مصرف از انبار ==================

    def process_miv_consumption(
            self,
            miv_id: int,
            warehouse_item_id: int,
            quantity: float,
            user_id: str,
            mto_item_id: int = None,
            allow_partial: bool = True,
            auto_create_shortage: bool = True,
            reserve_before_consume: bool = False,
            notes: str = None
    ) -> Dict[str, Any]:
        """
        پردازش کامل مصرف کالا از انبار بر اساس MIV

        Args:
            miv_id: شناسه MIV
            warehouse_item_id: شناسه آیتم انبار
            quantity: مقدار درخواستی
            user_id: شناسه کاربر
            mto_item_id: شناسه آیتم MTO (اختیاری)
            allow_partial: اجازه مصرف جزئی
            auto_create_shortage: ایجاد خودکار رکورد کسری
            reserve_before_consume: ابتدا رزرو سپس مصرف
            notes: یادداشت اضافی

        Returns:
            # ادامه‌ی فایل data/consumption_service.py از متد process_miv_consumption
            دیکشنری شامل:
            {
                "success": bool,
                "message": str,
                "consumed_qty": float,
                "shortage_qty": float,
                "transaction_id": int | None,
                "status": str
            }
        """
        session = self.session_factory()
        result = {
            "success": False,
            "message": "",
            "consumed_qty": 0.0,
            "shortage_qty": 0.0,
            "transaction_id": None,
            "status": TransactionStatus.FAILED.value,
        }

        try:
            miv = session.query(MIVRecord).get(miv_id)
            if not miv:
                result["message"] = f"MIV #{miv_id} یافت نشد."
                return result

            item = session.query(InventoryItem).get(warehouse_item_id)
            if not item:
                result["message"] = f"کالای انبار با ID={warehouse_item_id} یافت نشد."
                return result

            available = item.available_qty
            consumption = quantity if available >= quantity else (available if allow_partial else 0)
            shortage = quantity - consumption if quantity > consumption else 0

            if consumption <= 0 and shortage > 0:
                result["message"] = f"موجودی کافی برای مصرف وجود ندارد. کسری: {shortage}"
                return result

            # اگر بخواهیم ابتدا رزرو کنیم
            reservation = None
            if reserve_before_consume:
                reservation = MaterialReservation(
                    inventory_item_id=item.id,
                    reservation_no=f"RES-{datetime.now().strftime('%y%m%d%H%M%S')}",
                    reserved_qty=consumption,
                    remaining_qty=0,
                    consumed_qty=consumption,
                    project_id=miv.project_id,
                    miv_record_id=miv_id,
                    line_no=miv.line_no,
                    status="CONSUMED",
                    reserved_by=user_id,
                    remarks="Reservation before consumption",
                )
                session.add(reservation)

            balance_before = item.physical_qty
            item.physical_qty -= consumption
            item.available_qty -= consumption
            item.last_issue_date = datetime.utcnow()

            tx = InventoryTransaction(
                warehouse_id=item.warehouse_id,
                inventory_item_id=item.id,
                transaction_type="OUT",
                quantity=consumption,
                unit_price=item.unit_price,
                total_value=item.unit_price * consumption,
                balance_before=balance_before,
                balance_after=item.physical_qty,
                reference_type="MIV",
                reference_id=miv_id,
                reference_no=miv.miv_tag,
                remarks=notes or f"Issue for MIV #{miv_id}",
                performed_by=user_id,
                created_at=datetime.utcnow(),
            )
            session.add(tx)
            session.flush()

            # ثبت در مصرف MTO
            if mto_item_id:
                consumption_log = MTOConsumption(
                    mto_item_id=mto_item_id,
                    miv_record_id=miv_id,
                    inventory_item_id=item.id,
                    used_qty=consumption,
                )
                session.add(consumption_log)

            # ایجاد کسری در صورت نیاز
            if shortage > 0 and auto_create_shortage:
                adj = InventoryAdjustment(
                    inventory_item_id=item.id,
                    adjustment_type="SHORTAGE",
                    quantity_before=balance_before,
                    quantity_after=item.physical_qty,
                    quantity_adjusted=-shortage,
                    reason=f"Shortage issued for MIV#{miv_id}",
                    performed_by=user_id,
                    created_at=datetime.utcnow(),
                )
                session.add(adj)
                self.logger.warning(
                    f"Shortage: {item.item_code} shortage={shortage} for MIV#{miv_id}"
                )

            # به‌روزرسانی وضعیت MIV
            miv.status = (
                "PARTIALLY_ISSUED" if shortage > 0 else "ISSUED"
            )
            miv.last_updated = datetime.utcnow()

            session.commit()

            result.update(
                {
                    "success": True,
                    "message": f"مصرف {consumption} انجام شد. کسری: {shortage}",
                    "consumed_qty": consumption,
                    "shortage_qty": shortage,
                    "transaction_id": tx.id,
                    "status": TransactionStatus.PARTIAL.value
                    if shortage > 0
                    else TransactionStatus.COMPLETED.value,
                }
            )

            self._log_activity(
                action="MIV_CONSUMPTION",
                details=f"Issued {consumption} of {item.item_code} from warehouse {item.warehouse_id} for MIV#{miv_id}",
                user=user_id,
            )

            return result
        except Exception as e:
            session.rollback()
            self.logger.error(f"Error in process_miv_consumption: {e}")
            result["message"] = str(e)
            return result
        finally:
            session.close()

    # ================== بخش 3: بازگشت مصرف ==================
    def rollback_consumption(self, transaction_id: int, user_id: str, reason: str = "") -> Tuple[bool, str]:
        """بازگشت مصرف (بازگرداندن به انبار)"""
        session = self.session_factory()
        try:
            tx = session.query(InventoryTransaction).get(transaction_id)
            if not tx:
                return False, "تراکنش یافت نشد."

            if tx.transaction_type != "OUT":
                return False, "تراکنش قابل بازگشت نیست."

            item = tx.inventory_item
            item.physical_qty += abs(tx.quantity)
            item.available_qty += abs(tx.quantity)
            item.updated_at = datetime.utcnow()

            rollback_tx = InventoryTransaction(
                warehouse_id=item.warehouse_id,
                inventory_item_id=item.id,
                transaction_type="RETURN",
                quantity=abs(tx.quantity),
                unit_price=item.unit_price,
                total_value=abs(tx.quantity) * item.unit_price,
                balance_before=tx.balance_after,
                balance_after=item.physical_qty,
                reference_type="ROLLBACK",
                reference_id=tx.id,
                remarks=f"Rollback: {reason}",
                performed_by=user_id,
            )
            session.add(rollback_tx)
            session.commit()

            self._log_activity(
                action="ROLLBACK_TRANSACTION",
                details=f"Rollback {transaction_id} reason={reason}",
                user=user_id,
            )
            return True, f"تراکنش {transaction_id} بازگشت داده شد."
        except Exception as e:
            session.rollback()
            self.logger.error(f"Error in rollback_consumption: {e}")
            return False, str(e)
        finally:
            session.close()

    # ================== بخش 4: گزارش مصرف ==================
    def get_consumption_report(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        warehouse_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """گزارش تراکنش‌های مصرف/بازگشت"""
        session = self.session_factory()
        try:
            q = session.query(InventoryTransaction).filter(
                InventoryTransaction.transaction_type.in_(["OUT", "RETURN"])
            )
            if start_date:
                q = q.filter(InventoryTransaction.transaction_date >= start_date)
            if end_date:
                q = q.filter(InventoryTransaction.transaction_date <= end_date)
            if warehouse_id:
                q = q.filter(InventoryTransaction.warehouse_id == warehouse_id)

            txs = q.order_by(desc(InventoryTransaction.transaction_date)).all()
            return {
                "count": len(txs),
                "transactions": [
                    {
                        "id": t.id,
                        "warehouse": t.warehouse.name if t.warehouse else None,
                        "item_code": t.inventory_item.item_code,
                        "description": t.inventory_item.description,
                        "type": t.transaction_type,
                        "qty": t.quantity,
                        "date": t.transaction_date.isoformat(),
                        "performed_by": t.performed_by,
                        "reference": f"{t.reference_type}#{t.reference_id}",
                    }
                    for t in txs
                ],
            }
        except Exception as e:
            self.logger.error(f"Error get_consumption_report: {e}")
            return {"error": str(e), "transactions": []}
        finally:
            session.close()

    def create_shortage_record(
            self,
            inventory_item_id: int,
            requested_qty: float,
            available_qty: float,
            miv_id: int,
            user_id: str,
            auto_create_purchase_request: bool = True
    ) -> Dict[str, Any]:
        """
        ایجاد رکورد کسری و درخواست خرید خودکار

        Args:
            inventory_item_id: شناسه آیتم انبار
            requested_qty: مقدار درخواستی
            available_qty: مقدار موجود
            miv_id: شناسه MIV
            user_id: کاربر ثبت‌کننده
            auto_create_purchase_request: ایجاد خودکار درخواست خرید

        Returns:
            اطلاعات کسری و درخواست خرید
        """
        session = self.session_factory()
        try:
            item = session.query(InventoryItem).get(inventory_item_id)
            if not item:
                return {"success": False, "message": "آیتم یافت نشد"}

            shortage_qty = requested_qty - available_qty

            # تعیین اولویت بر اساس میزان کسری و موجودی بحرانی
            if available_qty == 0:
                priority = ShortageManagement.CRITICAL
            elif shortage_qty > item.min_stock_level:
                priority = ShortageManagement.HIGH
            elif shortage_qty > (item.min_stock_level * 0.5):
                priority = ShortageManagement.MEDIUM
            else:
                priority = ShortageManagement.LOW

            # ایجاد رکورد تعدیل موجودی به عنوان کسری
            shortage_adjustment = InventoryAdjustment(
                inventory_item_id=inventory_item_id,
                adjustment_type="SHORTAGE",
                adjustment_date=datetime.utcnow(),
                quantity_before=available_qty,
                quantity_after=available_qty,  # موجودی تغییر نمی‌کند، فقط ثبت کسری
                quantity_adjusted=0,
                reason=f"کسری {shortage_qty:.2f} واحد برای MIV#{miv_id}",
                reference_document=f"MIV-{miv_id}",
                performed_by=user_id,
                approved_by=None,  # نیاز به تأیید
                created_at=datetime.utcnow()
            )
            session.add(shortage_adjustment)

            # ایجاد درخواست خرید خودکار
            purchase_request = None
            if auto_create_purchase_request:
                purchase_request = self._create_purchase_request(
                    session=session,
                    item=item,
                    shortage_qty=shortage_qty,
                    priority=priority,
                    reference_doc=f"MIV-{miv_id}",
                    requested_by=user_id
                )

            session.commit()

            self._log_activity(
                action="SHORTAGE_RECORDED",
                details=f"Shortage {shortage_qty} for {item.item_code} (Priority: {priority})",
                user=user_id
            )

            return {
                "success": True,
                "shortage_id": shortage_adjustment.id,
                "shortage_qty": shortage_qty,
                "priority": priority,
                "purchase_request_created": purchase_request is not None,
                "message": f"کسری {shortage_qty:.2f} واحد ثبت شد"
            }

        except Exception as e:
            session.rollback()
            self.logger.error(f"Error creating shortage record: {e}")
            return {"success": False, "message": str(e)}
        finally:
            session.close()

    def _create_purchase_request(
            self,
            session: Session,
            item: InventoryItem,
            shortage_qty: float,
            priority: str,
            reference_doc: str,
            requested_by: str
    ) -> Optional[Dict]:
        """
        ایجاد درخواست خرید برای کسری

        توجه: این متد می‌تواند با سیستم خرید موجود integrate شود
        """
        try:
            # محاسبه مقدار سفارش بهینه
            reorder_qty = self._calculate_optimal_order_quantity(
                item=item,
                shortage_qty=shortage_qty
            )

            purchase_request = {
                "item_code": item.item_code,
                "description": item.description,
                "warehouse_id": item.warehouse_id,
                "current_stock": item.available_qty,
                "shortage_qty": shortage_qty,
                "requested_qty": reorder_qty,
                "unit": item.unit,
                "priority": priority,
                "reference": reference_doc,
                "requested_by": requested_by,
                "requested_at": datetime.utcnow().isoformat(),
                "estimated_price": reorder_qty * (item.unit_price or 0),
                "status": "PENDING_APPROVAL"
            }

            # TODO: در اینجا می‌توان با جدول Purchase Request واقعی کار کرد
            # مثلاً: session.add(PurchaseRequest(...))

            self.logger.info(f"Purchase request created for {item.item_code}: {reorder_qty} units")

            return purchase_request

        except Exception as e:
            self.logger.error(f"Error creating purchase request: {e}")
            return None

    def _calculate_optimal_order_quantity(
            self,
            item: InventoryItem,
            shortage_qty: float
    ) -> float:
        """
        محاسبه مقدار بهینه سفارش با در نظر گرفتن:
        - کسری فعلی
        - حداقل و حداکثر موجودی
        - نقطه سفارش مجدد
        """
        # مقدار پایه = کسری + رسیدن به حداقل موجودی
        base_qty = shortage_qty + (item.min_stock_level or 0)

        # اگر نقطه سفارش مجدد تعریف شده
        if item.reorder_point and item.available_qty < item.reorder_point:
            reorder_to_max = (item.max_stock_level or item.reorder_point * 2) - item.available_qty
            base_qty = max(base_qty, reorder_to_max)

        # گرد کردن به بالا (معمولاً به ده‌تایی یا صدتایی)
        if base_qty < 10:
            return float(base_qty)
        elif base_qty < 100:
            return float((base_qty // 10 + 1) * 10)
        else:
            return float((base_qty // 100 + 1) * 100)

    # ================== بخش 6: آنالیز و یادگیری ==================

    def analyze_feedback_patterns(
            self,
            days_back: int = 30,
            min_occurrences: int = 3
    ) -> List[Dict[str, Any]]:
        """
        تحلیل الگوهای انتخاب کاربران برای شناسایی mappingهای پرتکرار

        Args:
            days_back: تعداد روز برای بررسی
            min_occurrences: حداقل تعداد تکرار برای شناسایی الگو

        Returns:
            لیست الگوهای شناسایی شده
        """
        session = self.session_factory()
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)

            # Query برای یافتن الگوهای تکراری
            patterns = session.query(
                MaterialSearchHistory.search_term,
                MaterialSearchHistory.selected_item_code,
                func.count(MaterialSearchHistory.id).label('occurrence_count'),
                func.avg(
                    func.cast(
                        func.json_extract(
                            MaterialSearchHistory.search_filters,
                            '$.confidence'
                        ),
                        Float
                    )
                ).label('avg_confidence')
            ).filter(
                MaterialSearchHistory.timestamp >= cutoff_date,
                MaterialSearchHistory.was_successful == True
            ).group_by(
                MaterialSearchHistory.search_term,
                MaterialSearchHistory.selected_item_code
            ).having(
                func.count(MaterialSearchHistory.id) >= min_occurrences
            ).all()

            result = []
            for pattern in patterns:
                # بررسی وجود mapping
                existing_mapping = session.query(ItemMapping).filter(
                    ItemMapping.source_code == pattern.search_term,
                    ItemMapping.target_code == pattern.selected_item_code
                ).first()

                result.append({
                    'source_code': pattern.search_term,
                    'target_code': pattern.selected_item_code,
                    'occurrences': pattern.occurrence_count,
                    'avg_confidence': float(pattern.avg_confidence or 0),
                    'has_mapping': existing_mapping is not None,
                    'mapping_active': existing_mapping.is_active if existing_mapping else False,
                    'recommendation': self._get_pattern_recommendation(
                        pattern.occurrence_count,
                        pattern.avg_confidence,
                        existing_mapping
                    )
                })

            return sorted(result, key=lambda x: x['occurrences'], reverse=True)

        except Exception as e:
            self.logger.error(f"Error analyzing feedback patterns: {e}")
            return []
        finally:
            session.close()

    def _get_pattern_recommendation(
            self,
            occurrences: int,
            avg_confidence: float,
            existing_mapping: Optional[ItemMapping]
    ) -> str:
        """تولید توصیه برای الگوی شناسایی شده"""
        if existing_mapping and existing_mapping.is_active:
            return "ALREADY_ACTIVE"
        elif existing_mapping and not existing_mapping.is_active:
            if occurrences >= 5 and avg_confidence >= 0.8:
                return "RECOMMEND_ACTIVATE"
            else:
                return "NEEDS_MORE_DATA"
        else:
            if occurrences >= 3 and avg_confidence >= 0.75:
                return "RECOMMEND_CREATE"
            else:
                return "MONITOR"

    def auto_learn_from_patterns(
            self,
            days_back: int = 30,
            min_occurrences: int = 5,
            min_confidence: float = 0.8,
            dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        یادگیری خودکار از الگوهای کاربران و ایجاد/فعالسازی mappingها

        Args:
            days_back: بازه زمانی بررسی
            min_occurrences: حداقل تکرار
            min_confidence: حداقل اطمینان
            dry_run: فقط شبیه‌سازی بدون تغییر واقعی

        Returns:
            گزارش عملیات
        """
        patterns = self.analyze_feedback_patterns(days_back, min_occurrences)

        created_count = 0
        activated_count = 0
        skipped_count = 0

        session = self.session_factory() if not dry_run else None

        try:
            for pattern in patterns:
                if pattern['avg_confidence'] < min_confidence:
                    skipped_count += 1
                    continue

                if pattern['recommendation'] == 'RECOMMEND_CREATE':
                    if not dry_run:
                        self._create_mapping_from_pattern(session, pattern)
                    created_count += 1

                elif pattern['recommendation'] == 'RECOMMEND_ACTIVATE':
                    if not dry_run:
                        self._activate_mapping_from_pattern(session, pattern)
                    activated_count += 1

                else:
                    skipped_count += 1

            if not dry_run and session:
                session.commit()

                self._log_activity(
                    action="AUTO_LEARN_PATTERNS",
                    details=f"Created: {created_count}, Activated: {activated_count}, Skipped: {skipped_count}",
                    user="System"
                )

            return {
                "success": True,
                "patterns_analyzed": len(patterns),
                "mappings_created": created_count,
                "mappings_activated": activated_count,
                "skipped": skipped_count,
                "dry_run": dry_run
            }

        except Exception as e:
            if session:
                session.rollback()
            self.logger.error(f"Error in auto-learning: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if session:
                session.close()

    def _create_mapping_from_pattern(self, session: Session, pattern: Dict):
        """ایجاد mapping جدید از الگوی شناسایی شده"""
        new_mapping = ItemMapping(
            source_code=pattern['source_code'],
            target_code=pattern['target_code'],
            mapping_type='AUTO_LEARNED',
            confidence_score=pattern['avg_confidence'],
            usage_count=pattern['occurrences'],
            is_active=True,
            created_by='System',
            notes=f"Auto-learned from {pattern['occurrences']} user selections"
        )
        session.add(new_mapping)

    def _activate_mapping_from_pattern(self, session: Session, pattern: Dict):
        """فعالسازی mapping موجود بر اساس الگو"""
        mapping = session.query(ItemMapping).filter(
            ItemMapping.source_code == pattern['source_code'],
            ItemMapping.target_code == pattern['target_code']
        ).first()

        if mapping:
            mapping.is_active = True
            mapping.confidence_score = max(mapping.confidence_score, pattern['avg_confidence'])
            mapping.usage_count = pattern['occurrences']
            mapping.notes = f"Auto-activated based on {pattern['occurrences']} successful uses"
            mapping.last_used = datetime.utcnow()

    # ================== بخش 7: گزارشات و آمار مصرف ==================

    def get_consumption_statistics(
            self,
            warehouse_id: Optional[int] = None,
            project_id: Optional[int] = None,
            date_from: Optional[datetime] = None,
            date_to: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        دریافت آمار مصرف کالاها

        Args:
            warehouse_id: فیلتر انبار
            project_id: فیلتر پروژه
            date_from: تاریخ شروع
            date_to: تاریخ پایان

        Returns:
            آمار کامل مصرف
        """
        session = self.session_factory()
        try:
            # Query پایه برای تراکنش‌های خروجی
            query = session.query(InventoryTransaction).filter(
                InventoryTransaction.transaction_type == 'OUT'
            )

            # اعمال فیلترها
            if warehouse_id:
                query = query.join(InventoryItem).filter(
                    InventoryItem.warehouse_id == warehouse_id
                )

            if project_id:
                query = query.filter(
                    InventoryTransaction.project_id == project_id
                )

            if date_from:
                query = query.filter(
                    InventoryTransaction.transaction_date >= date_from
                )

            if date_to:
                query = query.filter(
                    InventoryTransaction.transaction_date <= date_to
                )

            transactions = query.all()

            # محاسبه آمار
            total_transactions = len(transactions)
            total_quantity = sum(t.quantity for t in transactions)
            total_value = sum(t.total_value or 0 for t in transactions)

            # آمار برگشتی‌ها
            return_query = session.query(InventoryTransaction).filter(
                InventoryTransaction.transaction_type == 'RETURN'
            )
            if date_from:
                return_query = return_query.filter(
                    InventoryTransaction.transaction_date >= date_from
                )
            if date_to:
                return_query = return_query.filter(
                    InventoryTransaction.transaction_date <= date_to
                )

            returns = return_query.all()
            total_returns = len(returns)
            return_quantity = sum(r.quantity for r in returns)

            # آمار کسری‌ها
            shortage_query = session.query(InventoryAdjustment).filter(
                InventoryAdjustment.adjustment_type == 'SHORTAGE'
            )
            if date_from:
                shortage_query = shortage_query.filter(
                    InventoryAdjustment.adjustment_date >= date_from
                )
            if date_to:
                shortage_query = shortage_query.filter(
                    InventoryAdjustment.adjustment_date <= date_to
                )

            shortages = shortage_query.count()

            # Top consumed items
            top_items = session.query(
                InventoryItem.item_code,
                InventoryItem.description,
                func.sum(InventoryTransaction.quantity).label('total_consumed'),
                func.count(InventoryTransaction.id).label('transaction_count')
            ).join(
                InventoryTransaction
            ).filter(
                InventoryTransaction.transaction_type == 'OUT'
            ).group_by(
                InventoryItem.item_code,
                InventoryItem.description
            ).order_by(
                func.sum(InventoryTransaction.quantity).desc()
            ).limit(10).all()

            return {
                "period": {
                    "from": date_from.isoformat() if date_from else None,
                    "to": date_to.isoformat() if date_to else None
                },
                "consumption": {
                    "total_transactions": total_transactions,
                    "total_quantity": float(total_quantity),
                    "total_value": float(total_value),
                    "average_per_transaction": float(
                        total_quantity / total_transactions) if total_transactions > 0 else 0
                },
                "returns": {
                    "total_returns": total_returns,
                    "return_quantity": float(return_quantity),
                    "return_rate": float(total_returns / total_transactions * 100) if total_transactions > 0 else 0
                },
                "shortages": {
                    "total_shortage_records": shortages
                },
                "top_consumed_items": [
                    {
                        "item_code": item.item_code,
                        "description": item.description,
                        "total_consumed": float(item.total_consumed),
                        "transaction_count": item.transaction_count
                    }
                    for item in top_items
                ]
            }

        except Exception as e:
            self.logger.error(f"Error getting consumption statistics: {e}")
            return {}
        finally:
            session.close()

    def get_item_consumption_history(
            self,
            item_code: str,
            warehouse_id: Optional[int] = None,
            limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        دریافت تاریخچه مصرف یک آیتم خاص

        Args:
            item_code: کد آیتم
            warehouse_id: فیلتر انبار
            limit: حداکثر تعداد رکورد

        Returns:
            لیست تراکنش‌های مصرف
        """
        session = self.session_factory()
        try:
            query = session.query(InventoryTransaction).join(
                InventoryItem
            ).filter(
                InventoryItem.item_code == item_code,
                InventoryTransaction.transaction_type.in_(['OUT', 'RETURN'])
            )

            if warehouse_id:
                query = query.filter(InventoryItem.warehouse_id == warehouse_id)

            transactions = query.order_by(
                InventoryTransaction.transaction_date.desc()
            ).limit(limit).all()

            return [
                {
                    "transaction_id": t.id,
                    "date": t.transaction_date.isoformat(),
                    "type": t.transaction_type,
                    "quantity": float(t.quantity),
                    "reference": t.reference_document,
                    "project_id": t.project_id,
                    "performed_by": t.performed_by,
                    "notes": t.notes
                }
                for t in transactions
            ]

        except Exception as e:
            self.logger.error(f"Error getting item consumption history: {e}")
            return []
        finally:
            session.close()

    # ================== بخش 8: متدهای کمکی ==================

    def _log_activity(self, action: str, details: str, user: str):
        """ثبت فعالیت در لاگ"""
        if self.activity_logger:
            try:
                self.activity_logger(
                    user=user,
                    action=action,
                    details=details
                )
            except Exception as e:
                self.logger.error(f"Error logging activity: {e}")

    def validate_consumption_request(
            self,
            miv_id: int,
            items: List[Dict[str, Any]]
    ) -> Tuple[bool, List[str]]:
        """
        اعتبارسنجی درخواست مصرف قبل از پردازش

        Args:
            miv_id: شناسه MIV
            items: لیست آیتم‌ها برای مصرف

        Returns:
            (آیا معتبر است, لیست خطاها)
        """
        errors = []
        session = self.session_factory()

        try:
            # بررسی وجود MIV
            miv = session.query(MIVRecord).get(miv_id)
            if not miv:
                errors.append(f"MIV با شناسه {miv_id} یافت نشد")
                return False, errors

            # بررسی وضعیت MIV
            if miv.status in ["CANCELLED", "CLOSED"]:
                errors.append(f"MIV در وضعیت {miv.status} است و قابل مصرف نیست")

            # بررسی آیتم‌ها
            for idx, item in enumerate(items, 1):
                if 'inventory_item_id' not in item:
                    errors.append(f"آیتم {idx}: شناسه آیتم انبار مشخص نشده")
                    continue

                if 'quantity' not in item or item['quantity'] <= 0:
                    errors.append(f"آیتم {idx}: مقدار نامعتبر")
                    continue

                # بررسی موجودی
                inv_item = session.query(InventoryItem).get(item['inventory_item_id'])
                if not inv_item:
                    errors.append(f"آیتم {idx}: آیتم انبار یافت نشد")
                elif inv_item.available_qty < item['quantity']:
                    errors.append(
                        f"آیتم {idx}: موجودی کافی نیست "
                        f"(موجود: {inv_item.available_qty}, درخواست: {item['quantity']})"
                    )

            return len(errors) == 0, errors

        except Exception as e:
            self.logger.error(f"Error validating consumption request: {e}")
            errors.append(f"خطای سیستمی: {str(e)}")
            return False, errors
        finally:
            session.close()

    def get_pending_shortages(
            self,
            warehouse_id: Optional[int] = None,
            priority: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        دریافت لیست کسری‌های در انتظار

        Args:
            warehouse_id: فیلتر انبار
            priority: فیلتر اولویت

        Returns:
            لیست کسری‌ها
        """
        session = self.session_factory()
        try:
            query = session.query(InventoryAdjustment).filter(
                InventoryAdjustment.adjustment_type == 'SHORTAGE',
                InventoryAdjustment.approved_by.is_(None)  # تأیید نشده
            )

            if warehouse_id:
                query = query.join(InventoryItem).filter(
                    InventoryItem.warehouse_id == warehouse_id
                )

            shortages = query.order_by(
                InventoryAdjustment.created_at.desc()
            ).all()

            result = []
            for shortage in shortages:
                # استخراج اولویت از reason یا metadata
                shortage_priority = self._extract_priority_from_shortage(shortage)

                if priority and shortage_priority != priority:
                    continue

                result.append({
                    "id": shortage.id,
                    "item_code": shortage.inventory_item.item_code,
                    "description": shortage.inventory_item.description,
                    "quantity": abs(shortage.quantity_adjusted),
                    "priority": shortage_priority,
                    "reference": shortage.reference_document,
                    "created_at": shortage.created_at.isoformat(),
                    "created_by": shortage.performed_by
                })

            return result

        except Exception as e:
            self.logger.error(f"Error getting pending shortages: {e}")
            return []
        finally:
            session.close()

    def _extract_priority_from_shortage(self, shortage: InventoryAdjustment) -> str:
        """استخراج اولویت از رکورد کسری"""
        # می‌توان priority را در reason یا یک فیلد JSON ذخیره کرد
        reason = shortage.reason or ""
        if "CRITICAL" in reason.upper():
            return ShortageManagement.CRITICAL
        elif "HIGH" in reason.upper():
            return ShortageManagement.HIGH
        elif "MEDIUM" in reason.upper():
            return ShortageManagement.MEDIUM
        else:
            return ShortageManagement.LOW

    def on_select_button_clicked(self):
        """هنگام انتخاب آیتم از انبار توسط کاربر"""
        # ... کد موجود برای انتخاب آیتم ...

        selected_item = self.get_selected_warehouse_item()
        if selected_item:
            # ثبت feedback کاربر
            self.consumption_service.record_user_feedback(
                mto_code=self.current_mto_item.item_code,
                warehouse_code=selected_item['item_code'],
                confidence=selected_item.get('similarity', 1.0),
                user=self.current_user or "operator",
                source=self._determine_source(selected_item),
                project_id=self.project_id
            )

            # ... ادامه کد موجود ...

    def _determine_source(self, item):
        """تعیین منبع انتخاب (NLP, Rule-based, Manual)"""
        if item.get('match_type') == 'nlp':
            return 'NLP_MATCH'
        elif item.get('match_type') == 'rule':
            return 'RULE_BASED'
        else:
            return 'MANUAL_SELECTION'

    def finalize_consumption(self):
        """نهایی کردن مصرف - متد جدید"""
        if self.selected_items:
            # استفاده از ConsumptionService برای پردازش نهایی
            result = self.consumption_service.process_miv_consumption(
                miv_id=self.miv_id,
                items=self.selected_items,
                user=self.current_user,
                allow_partial=True
            )

            if result['success']:
                QMessageBox.information(
                    self,
                    "موفق",
                    f"مصرف با موفقیت ثبت شد.\n"
                    f"تعداد تراکنش‌ها: {len(result['transaction_ids'])}"
                )

                # نمایش کسری‌ها در صورت وجود
                if result.get('shortages'):
                    shortage_text = "\n".join([
                        f"- {s['item_code']}: {s['shortage_qty']} {s['unit']}"
                        for s in result['shortages']
                    ])
                    QMessageBox.warning(
                        self,
                        "کسری موجودی",
                        f"کسری‌های زیر ثبت شد:\n{shortage_text}"
                    )

                self.accept()
            else:
                QMessageBox.critical(
                    self,
                    "خطا",
                    f"خطا در ثبت مصرف: {result.get('error', 'Unknown error')}"
                )

