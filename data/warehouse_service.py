# file: data/warehouse_service.py
"""
سرویس مدیریت انبار
شامل عملیات CRUD انبار، موجودی، رزرو و تراکنش‌ها
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

# import از models.py
from models import (
    Base,
    Project,
    MIVRecord,
    Warehouse,
    InventoryItem,
    InventoryTransaction,
    MaterialReservation,
    InventoryAdjustment
)


class WarehouseService:
    """سرویس مدیریت انبار"""

    def __init__(self, session_factory, activity_logger=None):
        """
        Args:
            session_factory: کارخانه ساخت Session
            activity_logger: تابع لاگ فعالیت‌ها (اختیاری)
        """
        self.session_factory = session_factory
        self.activity_logger = activity_logger

    def _log_activity(self, action: str, details: str = "", user: str = "System"):
        """ثبت لاگ فعالیت"""
        if self.activity_logger:
            self.activity_logger(user=user, action=action, details=details)

    def _generate_reservation_no(self, session: Session) -> str:
        """تولید شماره رزرو یکتا"""
        # آخرین شماره رزرو
        last_reservation = session.query(MaterialReservation) \
            .order_by(MaterialReservation.id.desc()).first()

        if last_reservation:
            last_no = int(last_reservation.reservation_no.split('-')[-1])
            new_no = last_no + 1
        else:
            new_no = 1

        return f"RES-{datetime.now().strftime('%Y%m')}-{new_no:05d}"

    # ================== مدیریت انبار ==================

    def create_warehouse(self, code: str, name: str, location: str = None) -> Warehouse:
        """ایجاد انبار جدید"""
        session = self.session_factory()
        try:
            # بررسی عدم تکرار کد
            existing = session.query(Warehouse).filter_by(code=code).first()
            if existing:
                raise ValueError(f"انبار با کد {code} قبلاً ثبت شده است")

            warehouse = Warehouse(
                code=code,
                name=name,
                location=location,
                is_active=True
            )

            session.add(warehouse)
            session.commit()
            session.refresh(warehouse)

            self._log_activity(
                action="CREATE_WAREHOUSE",
                details=f"انبار {name} با کد {code} ایجاد شد"
            )

            return warehouse

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get_all_warehouses(self, active_only: bool = True) -> List[Warehouse]:
        """دریافت لیست انبارها"""
        session = self.session_factory()
        try:
            query = session.query(Warehouse)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.order_by(Warehouse.code).all()
        finally:
            session.close()

    def get_warehouse_by_code(self, code: str) -> Optional[Warehouse]:
        """دریافت انبار با کد"""
        session = self.session_factory()
        try:
            return session.query(Warehouse).filter_by(code=code).first()
        finally:
            session.close()

    def update_warehouse(self, warehouse_id: int, **kwargs) -> Warehouse:
        """به‌روزرسانی اطلاعات انبار"""
        session = self.session_factory()
        try:
            warehouse = session.query(Warehouse).get(warehouse_id)
            if not warehouse:
                raise ValueError(f"انبار با شناسه {warehouse_id} یافت نشد")

            for key, value in kwargs.items():
                if hasattr(warehouse, key):
                    setattr(warehouse, key, value)

            warehouse.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(warehouse)

            self._log_activity(
                action="UPDATE_WAREHOUSE",
                details=f"انبار {warehouse.name} به‌روزرسانی شد"
            )

            return warehouse

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    # ================== مدیریت موجودی ==================

    def add_inventory_item(
            self,
            warehouse_code: str,
            item_code: str,  # تغییر نام پارامتر
            description: str,
            size: str = None,
            unit: str = "EA",
            initial_qty: float = 0,
            type: str = None,  # اضافه شد
            warehouse_item_number: str = None  # اضافه شد
    ) -> InventoryItem:
        """اضافه کردن کالای جدید به انبار"""
        session = self.session_factory()
        try:
            # یافتن انبار
            warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
            if not warehouse:
                raise ValueError(f"انبار {warehouse_code} یافت نشد")

            # بررسی تکراری نبودن
            existing = session.query(InventoryItem).filter_by(
                warehouse_id=warehouse.id,
                item_code=item_code,  # تغییر
                size=size
            ).first()

            if existing:
                raise ValueError(f"کالای {item_code} با سایز {size} قبلاً در انبار موجود است")

            # ایجاد آیتم جدید
            item = InventoryItem(
                warehouse_id=warehouse.id,
                item_code=item_code,  # تغییر
                description=description,
                size=size,
                type=type,  # اضافه شد
                warehouse_item_number=warehouse_item_number,  # اضافه شد
                unit=unit,
                available_qty=initial_qty,
                reserved_qty=0,  # اضافه شد
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )

            session.add(item)

            # ثبت تراکنش ورودی اولیه
            if initial_qty > 0:
                transaction = InventoryTransaction(
                    warehouse_id=warehouse.id,
                    inventory_item_id=item.id,
                    transaction_type="IN",
                    quantity=initial_qty,
                    reference_type="INITIAL",
                    performed_by="System",
                    timestamp=datetime.utcnow(),
                    notes="موجودی اولیه"
                )
                session.add(transaction)
                item.last_transaction_date = datetime.utcnow()

            session.commit()
            self.log_activity("System", "INVENTORY_ADD",
                              f"کالای {item_code} به انبار {warehouse_code} اضافه شد")

            return item

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def update_inventory_item(
            self,
            item_id: int,
            description: str = None,
            size: str = None,
            unit: str = None,
            type: str = None,  # اضافه شد
            warehouse_item_number: str = None  # اضافه شد
    ) -> InventoryItem:
        """به‌روزرسانی اطلاعات کالا"""
        session = self.session_factory()
        try:
            item = session.query(InventoryItem).filter_by(id=item_id).first()
            if not item:
                raise ValueError(f"کالای با شناسه {item_id} یافت نشد")

            if description is not None:
                item.description = description
            if size is not None:
                item.size = size
            if unit is not None:
                item.unit = unit
            if type is not None:  # اضافه شد
                item.type = type
            if warehouse_item_number is not None:  # اضافه شد
                item.warehouse_item_number = warehouse_item_number

            item.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(item)

            return item

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get_inventory_items(
            self,
            warehouse_code: str = None,
            item_code: str = None,  # تغییر نام پارامتر
            low_stock_only: bool = False
    ) -> List[InventoryItem]:
        """جستجوی کالاها در انبار"""
        session = self.session_factory()
        try:
            query = session.query(InventoryItem)

            if warehouse_code:
                warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
                if warehouse:
                    query = query.filter_by(warehouse_id=warehouse.id)

            if item_code:  # تغییر
                query = query.filter(InventoryItem.item_code.like(f"%{item_code}%"))  # تغییر

            # حذف فیلتر low_stock_only چون min_stock_level در مدل نداریم
            # یا می‌توانیم بر اساس یک مقدار ثابت یا reserved_qty فیلتر کنیم
            if low_stock_only:
                # مثلاً آیتم‌هایی که موجودی‌شان کمتر از 10 است
                query = query.filter(InventoryItem.available_qty <= 10)

            return query.order_by(InventoryItem.item_code).all()  # تغییر

        finally:
            session.close()

    def get_inventory_by_material(
            self,
            warehouse_code: str,
            item_code: str,  # تغییر نام پارامتر
            size: str = None
    ) -> Optional[InventoryItem]:
        """دریافت موجودی یک کالا"""
        session = self.session_factory()
        try:
            warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
            if not warehouse:
                return None

            query = session.query(InventoryItem).filter_by(
                warehouse_id=warehouse.id,
                item_code=item_code  # تغییر
            )

            if size:
                query = query.filter_by(size=size)

            return query.first()

        finally:
            session.close()

    def check_availability(
            self,
            warehouse_code: str,
            item_code: str,  # تغییر نام پارامتر
            required_qty: float,
            size: str = None
    ) -> Tuple[bool, float]:
        """بررسی موجودی کالا"""
        item = self.get_inventory_by_material(warehouse_code, item_code, size)  # تغییر
        if not item:
            return False, 0

        available = item.available_qty
        return available >= required_qty, available

    # ================== عملیات رزرو ==================

    def reserve_material(
            self,
            warehouse_code: str,
            item_code: str,  # تغییر نام پارامتر
            quantity: float,
            mto_item_id: int = None,  # تغییر از miv_record_id
            reserved_by: str = None,
            expiry_days: int = 30,
            notes: str = None
    ) -> MaterialReservation:
        """رزرو کالا"""
        session = self.session_factory()
        try:
            # پیدا کردن کالا
            warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
            if not warehouse:
                raise ValueError(f"انبار {warehouse_code} یافت نشد")

            item = session.query(InventoryItem).filter_by(
                warehouse_id=warehouse.id,
                item_code=item_code  # تغییر
            ).first()

            if not item:
                raise ValueError(f"کالای {item_code} در انبار یافت نشد")

            # بررسی موجودی
            if item.available_qty < quantity:
                raise ValueError(f"موجودی کافی نیست. موجود: {item.available_qty}")

            # ایجاد رزرو
            reservation = MaterialReservation(
                inventory_item_id=item.id,
                mto_item_id=mto_item_id,  # استفاده از mto_item_id
                reserved_qty=quantity,
                reserved_by=reserved_by or "System",
                reservation_date=datetime.utcnow(),
                expiry_date=datetime.utcnow() + timedelta(days=expiry_days),
                status="ACTIVE",
                consumed_qty=0,
                notes=notes
            )

            session.add(reservation)

            # به‌روزرسانی موجودی
            item.available_qty -= quantity
            item.reserved_qty = (item.reserved_qty or 0) + quantity  # اضافه شد
            item.updated_at = datetime.utcnow()

            # ثبت تراکنش
            transaction = InventoryTransaction(
                warehouse_id=warehouse.id,
                inventory_item_id=item.id,
                transaction_type="RESERVATION",
                quantity=quantity,
                reference_type="RESERVATION",
                reference_id=reservation.id,
                performed_by=reserved_by or "System",
                timestamp=datetime.utcnow(),
                notes=f"رزرو برای {notes or 'نامشخص'}"
            )
            session.add(transaction)

            item.last_transaction_date = datetime.utcnow()

            session.commit()

            self.log_activity(
                reserved_by or "System",
                "MATERIAL_RESERVE",
                f"رزرو {quantity} {item.unit} از {item_code}"
            )

            return reservation

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def cancel_reservation(self, reservation_id: int, cancelled_by: str = None) -> bool:
        """لغو رزرو"""
        session = self.session_factory()
        try:
            reservation = session.query(MaterialReservation).get(reservation_id)
            if not reservation:
                raise ValueError(f"رزرو با شناسه {reservation_id} یافت نشد")

            if reservation.status != 'ACTIVE':
                raise ValueError("فقط رزروهای فعال قابل لغو هستند")

            # آزادسازی موجودی
            item = session.query(InventoryItem).get(reservation.inventory_item_id)
            remaining = reservation.reserved_qty - reservation.consumed_qty

            item.reserved_qty -= remaining
            item.available_qty = item.physical_qty - item.reserved_qty
            item.updated_at = datetime.utcnow()

            # به‌روزرسانی وضعیت رزرو
            reservation.status = 'CANCELLED'
            reservation.updated_at = datetime.utcnow()

            session.commit()

            self._log_activity(
                action="CANCEL_RESERVATION",
                details=f"لغو رزرو {reservation.reservation_no}"
            )

            return True

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def consume_reservation(self, reservation_id: int, consume_qty: float) -> InventoryTransaction:
        """مصرف از رزرو"""
        session = self.session_factory()
        try:
            reservation = session.query(MaterialReservation).get(reservation_id)
            if not reservation:
                raise ValueError(f"رزرو با شناسه {reservation_id} یافت نشد")

            if reservation.status != 'ACTIVE':
                raise ValueError("رزرو غیرفعال است")

            if consume_qty > reservation.remaining_qty:
                raise ValueError(
                    f"مقدار درخواستی {consume_qty} از باقیمانده رزرو {reservation.remaining_qty} بیشتر است")

            # دریافت کالا
            item = session.query(InventoryItem).get(reservation.inventory_item_id)

            # ایجاد تراکنش خروج
            transaction = InventoryTransaction(
                warehouse_id=item.warehouse.id,
                inventory_item_id=item.id,
                transaction_type="OUT",
                quantity=consume_qty,
                unit_price=item.unit_price,
                total_value=consume_qty * item.unit_price,
                balance_before=item.physical_qty,
                balance_after=item.physical_qty - consume_qty,
                reference_type="RESERVATION",
                reference_id=reservation.id,
                reference_no=reservation.reservation_no,
                remarks=f"مصرف از رزرو {reservation.reservation_no}"
            )
            session.add(transaction)

            # به‌روزرسانی رزرو
            reservation.consumed_qty += consume_qty
            reservation.remaining_qty -= consume_qty

            if reservation.remaining_qty == 0:
                reservation.status = 'CONSUMED'

            reservation.updated_at = datetime.utcnow()

            # به‌روزرسانی موجودی
            item.physical_qty -= consume_qty
            item.reserved_qty -= consume_qty
            item.available_qty = item.physical_qty - item.reserved_qty
            item.last_issue_date = datetime.utcnow()
            item.updated_at = datetime.utcnow()

            session.commit()
            session.refresh(transaction)

            self._log_activity(
                action="CONSUME_RESERVATION",
                details=f"مصرف {consume_qty} از رزرو {reservation.reservation_no}"
            )

            return transaction

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get_active_reservations(self, project_id: int = None,
                                material_code: str = None) -> List[MaterialReservation]:
        """دریافت رزروهای فعال"""
        session = self.session_factory()
        try:
            query = session.query(MaterialReservation).filter_by(status='ACTIVE')

            if project_id:
                query = query.filter_by(project_id=project_id)

            if material_code:
                query = query.join(InventoryItem).filter(
                    InventoryItem.material_code.like(f"%{material_code}%")
                )

            return query.order_by(MaterialReservation.reservation_date.desc()).all()

        finally:
            session.close()

    # ================== تراکنش‌های انبار ==================

    def record_inventory_in(
            self,
            warehouse_code: str,
            item_code: str,  # تغییر
            quantity: float,
            size: str = None,
            description: str = None,
            unit: str = "EA",
            type: str = None,  # اضافه شد
            warehouse_item_number: str = None,  # اضافه شد
            reference_type: str = "PURCHASE",
            reference_id: int = None,
            performed_by: str = None,
            notes: str = None
    ) -> InventoryTransaction:
        """ثبت ورود کالا به انبار"""
        session = self.session_factory()
        try:
            warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
            if not warehouse:
                raise ValueError(f"انبار {warehouse_code} یافت نشد")

            # جستجوی کالا یا ایجاد آن
            item = session.query(InventoryItem).filter_by(
                warehouse_id=warehouse.id,
                item_code=item_code,  # تغییر
                size=size
            ).first()

            if not item:
                # ایجاد کالای جدید
                item = InventoryItem(
                    warehouse_id=warehouse.id,
                    item_code=item_code,  # تغییر
                    description=description or f"کالای {item_code}",
                    size=size,
                    type=type,  # اضافه شد
                    warehouse_item_number=warehouse_item_number,  # اضافه شد
                    unit=unit,
                    available_qty=0,
                    reserved_qty=0,  # اضافه شد
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                session.add(item)
                session.flush()

            # به‌روزرسانی موجودی
            item.available_qty += quantity
            item.last_transaction_date = datetime.utcnow()
            item.updated_at = datetime.utcnow()

            # ثبت تراکنش
            transaction = InventoryTransaction(
                warehouse_id=warehouse.id,
                inventory_item_id=item.id,
                transaction_type="IN",
                quantity=quantity,
                reference_type=reference_type,
                reference_id=reference_id,
                performed_by=performed_by or "System",
                timestamp=datetime.utcnow(),
                notes=notes
            )

            session.add(transaction)
            session.commit()

            self.log_activity(
                performed_by or "System",
                "INVENTORY_IN",
                f"ورود {quantity} {unit} از {item_code} به انبار {warehouse_code}"
            )

            return transaction

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def record_inventory_out(
            self,
            warehouse_code: str,
            item_code: str,  # تغییر
            quantity: float,
            size: str = None,
            reference_type: str = "CONSUMPTION",
            reference_id: int = None,
            performed_by: str = None,
            notes: str = None
    ) -> InventoryTransaction:
        """ثبت خروج کالا از انبار"""
        session = self.session_factory()
        try:
            warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
            if not warehouse:
                raise ValueError(f"انبار {warehouse_code} یافت نشد")

            # جستجوی کالا
            item = session.query(InventoryItem).filter_by(
                warehouse_id=warehouse.id,
                item_code=item_code,  # تغییر
                size=size
            ).first()

            if not item:
                raise ValueError(f"کالای {item_code} در انبار {warehouse_code} یافت نشد")

            if item.available_qty < quantity:
                raise ValueError(f"موجودی کافی نیست. موجودی فعلی: {item.available_qty}")

            # به‌روزرسانی مقدار موجودی
            item.available_qty -= quantity
            item.last_transaction_date = datetime.utcnow()
            item.updated_at = datetime.utcnow()

            # ثبت تراکنش خروج
            transaction = InventoryTransaction(
                warehouse_id=warehouse.id,
                inventory_item_id=item.id,
                transaction_type="OUT",
                quantity=quantity,
                reference_type=reference_type,
                reference_id=reference_id,
                performed_by=performed_by or "System",
                timestamp=datetime.utcnow(),
                notes=notes
            )

            session.add(transaction)
            session.commit()

            self.log_activity(
                performed_by or "System",
                "INVENTORY_OUT",
                f"خروج {quantity} {item.unit} از {item_code} از انبار {warehouse_code}"
            )

            return transaction

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def adjust_inventory(self, warehouse_code: str, material_code: str,
                         new_quantity: float, adjustment_type: str,
                         reason: str, performed_by: str,
                         reference_document: str = None,
                         size: str = None, heat_no: str = None) -> InventoryAdjustment:
        """تعدیل موجودی انبار"""
        session = self.session_factory()
        try:
            # پیدا کردن آیتم
            warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
            if not warehouse:
                raise ValueError(f"انبار {warehouse_code} یافت نشد")

            item = session.query(InventoryItem).filter_by(
                warehouse_id=warehouse.id,
                material_code=material_code,
                size=size,
                heat_no=heat_no
            ).first()

            if not item:
                raise ValueError(f"کالای {material_code} در انبار یافت نشد")

            # ثبت تعدیل
            quantity_before = item.physical_qty
            quantity_adjusted = new_quantity - quantity_before

            adjustment = InventoryAdjustment(
                inventory_item_id=item.id,
                adjustment_type=adjustment_type,
                quantity_before=quantity_before,
                quantity_after=new_quantity,
                quantity_adjusted=quantity_adjusted,
                reason=reason,
                reference_document=reference_document,
                performed_by=performed_by
            )
            session.add(adjustment)

            # ایجاد تراکنش تعدیل
            transaction = InventoryTransaction(
                warehouse_id=warehouse.id,
                inventory_item_id=item.id,
                transaction_type="ADJUST",
                quantity=abs(quantity_adjusted),
                balance_before=quantity_before,
                balance_after=new_quantity,
                reference_type="ADJUSTMENT",
                reference_no=reference_document,
                performed_by=performed_by,
                remarks=f"تعدیل موجودی: {reason}"
            )
            session.add(transaction)

            # به‌روزرسانی موجودی
            item.physical_qty = new_quantity
            item.available_qty = item.physical_qty - item.reserved_qty
            item.updated_at = datetime.utcnow()

            session.commit()
            session.refresh(adjustment)

            self._log_activity(
                action="INVENTORY_ADJUSTMENT",
                details=f"تعدیل موجودی {material_code} از {quantity_before} به {new_quantity}"
            )

            return adjustment

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get_transactions_history(self, warehouse_code: str = None,
                                 material_code: str = None,
                                 transaction_type: str = None,
                                 from_date: datetime = None,
                                 to_date: datetime = None) -> List[InventoryTransaction]:
        """دریافت تاریخچه تراکنش‌ها"""
        session = self.session_factory()
        try:
            query = session.query(InventoryTransaction)

            if warehouse_code:
                warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
                if warehouse:
                    query = query.filter_by(warehouse_id=warehouse.id)

            if material_code:
                query = query.join(InventoryItem).filter(
                    InventoryItem.material_code.like(f"%{material_code}%")
                )

            if transaction_type:
                query = query.filter_by(transaction_type=transaction_type)

            if from_date:
                query = query.filter(InventoryTransaction.transaction_date >= from_date)

            if to_date:
                query = query.filter(InventoryTransaction.transaction_date <= to_date)

            return query.order_by(InventoryTransaction.transaction_date.desc()).all()

        finally:
            session.close()

    # ================== گزارش‌گیری ==================

    def get_inventory_summary(self, warehouse_code: str = None) -> Dict[str, Any]:
        """خلاصه وضعیت انبار"""
        session = self.session_factory()
        try:
            query = session.query(InventoryItem)

            if warehouse_code:
                warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
                if warehouse:
                    query = query.filter_by(warehouse_id=warehouse.id)

            items = query.all()

            total_items = len(items)
            total_value = sum(item.total_value or 0 for item in items)
            low_stock_items = [item for item in items if item.available_qty <= item.min_stock_level]

            return {
                'total_items': total_items,
                'total_value': total_value,
                'low_stock_count': len(low_stock_items),
                'low_stock_items': low_stock_items,
                'warehouses': self.get_all_warehouses()
            }

        finally:
            session.close()

    def get_stock_movement_report(self, warehouse_code: str,
                                  from_date: datetime, to_date: datetime) -> Dict[str, Any]:
        """گزارش گردش کالا"""
        session = self.session_factory()
        try:
            warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
            if not warehouse:
                return {}

            # تراکنش‌های دوره
            transactions = session.query(InventoryTransaction).filter(
                InventoryTransaction.warehouse_id == warehouse.id,
                InventoryTransaction.transaction_date >= from_date,
                InventoryTransaction.transaction_date <= to_date
            ).all()

            # محاسبات
            total_in = sum(t.quantity for t in transactions if t.transaction_type == 'IN')
            total_out = sum(t.quantity for t in transactions if t.transaction_type == 'OUT')
            total_adjust = sum(t.quantity for t in transactions if t.transaction_type == 'ADJUST')

            # گروه‌بندی بر اساس کالا
            from collections import defaultdict
            item_movements = defaultdict(lambda: {'in': 0, 'out': 0, 'adjust': 0})

            for trans in transactions:
                item = session.query(InventoryItem).get(trans.inventory_item_id)
                if item:
                    key = item.material_code
                    if trans.transaction_type == 'IN':
                        item_movements[key]['in'] += trans.quantity
                    elif trans.transaction_type == 'OUT':
                        item_movements[key]['out'] += trans.quantity
                    elif trans.transaction_type == 'ADJUST':
                        item_movements[key]['adjust'] += trans.quantity

            return {
                'warehouse': warehouse.name,
                'period': {
                    'from': from_date,
                    'to': to_date
                },
                'summary': {
                    'total_in': total_in,
                    'total_out': total_out,
                    'total_adjust': total_adjust,
                    'net_movement': total_in - total_out
                },
                'item_movements': dict(item_movements),
                'transactions': transactions
            }

        finally:
            session.close()

    def get_low_stock_items(self, warehouse_code: str = None) -> List[InventoryItem]:
        """دریافت اقلام با موجودی کم"""
        session = self.session_factory()
        try:
            query = session.query(InventoryItem).filter(
                InventoryItem.available_qty <= InventoryItem.min_stock_level
            )

            if warehouse_code:
                warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
                if warehouse:
                    query = query.filter_by(warehouse_id=warehouse.id)

            return query.all()

        finally:
            session.close()

    def get_inventory_valuation(self, warehouse_code: str = None,
                                as_of_date: datetime = None) -> Dict[str, Any]:
        """ارزش‌گذاری موجودی انبار"""
        session = self.session_factory()
        try:
            query = session.query(InventoryItem)

            if warehouse_code:
                warehouse = session.query(Warehouse).filter_by(code=warehouse_code).first()
                if warehouse:
                    query = query.filter_by(warehouse_id=warehouse.id)

            if as_of_date:
                # فقط آیتم‌هایی که قبل از تاریخ مشخص ایجاد شده‌اند
                query = query.filter(InventoryItem.created_at <= as_of_date)

            items = query.all()

            # محاسبه ارزش
            total_qty = sum(item.physical_qty for item in items)
            total_value = sum(item.total_value or 0 for item in items)

            # گروه‌بندی بر اساس کد کالا
            from collections import defaultdict
            grouped = defaultdict(lambda: {'qty': 0, 'value': 0, 'items': []})

            for item in items:
                key = item.material_code
                grouped[key]['qty'] += item.physical_qty
                grouped[key]['value'] += item.total_value or 0
                grouped[key]['items'].append(item)

            return {
                'as_of_date': as_of_date or datetime.utcnow(),
                'total_items': len(items),
                'total_quantity': total_qty,
                'total_value': total_value,
                'by_material': dict(grouped),
                'warehouses': [w.name for w in set(item.warehouse for item in items)]
            }

        finally:
            session.close()

    def transfer_between_warehouses(self, from_warehouse_code: str, to_warehouse_code: str,
                                    material_code: str, quantity: float,
                                    transfer_no: str, performed_by: str,
                                    size: str = None, heat_no: str = None) -> Tuple[
        InventoryTransaction, InventoryTransaction]:
        """انتقال کالا بین انبارها"""
        session = self.session_factory()
        try:
            # انبار مبدا
            from_warehouse = session.query(Warehouse).filter_by(code=from_warehouse_code).first()
            if not from_warehouse:
                raise ValueError(f"انبار مبدا {from_warehouse_code} یافت نشد")

            # انبار مقصد
            to_warehouse = session.query(Warehouse).filter_by(code=to_warehouse_code).first()
            if not to_warehouse:
                raise ValueError(f"انبار مقصد {to_warehouse_code} یافت نشد")

            # آیتم در انبار مبدا
            from_item = session.query(InventoryItem).filter_by(
                warehouse_id=from_warehouse.id,
                material_code=material_code,
                size=size,
                heat_no=heat_no
            ).first()

            if not from_item:
                raise ValueError(f"کالای {material_code} در انبار مبدا یافت نشد")

            if from_item.available_qty < quantity:
                raise ValueError(f"موجودی کافی نیست. موجود: {from_item.available_qty}")

            # آیتم در انبار مقصد (ایجاد اگر وجود ندارد)
            to_item = session.query(InventoryItem).filter_by(
                warehouse_id=to_warehouse.id,
                material_code=material_code,
                size=size,
                heat_no=heat_no
            ).first()

            if not to_item:
                to_item = InventoryItem(
                    warehouse_id=to_warehouse.id,
                    material_code=material_code,
                    material_description=from_item.material_description,
                    size=size,
                    heat_no=heat_no,
                    unit=from_item.unit,
                    physical_qty=0,
                    available_qty=0,
                    reserved_qty=0,
                    unit_price=from_item.unit_price
                )
                session.add(to_item)
                session.flush()

            # تراکنش خروج از انبار مبدا
            out_transaction = InventoryTransaction(
                warehouse_id=from_warehouse.id,
                inventory_item_id=from_item.id,
                transaction_type="TRANSFER_OUT",
                quantity=quantity,
                unit_price=from_item.unit_price,
                total_value=quantity * from_item.unit_price,
                balance_before=from_item.physical_qty,
                balance_after=from_item.physical_qty - quantity,
                reference_type="TRANSFER",
                reference_no=transfer_no,
                performed_by=performed_by,
                remarks=f"انتقال به انبار {to_warehouse.name}"
            )
            session.add(out_transaction)

            # تراکنش ورود به انبار مقصد
            in_transaction = InventoryTransaction(
                warehouse_id=to_warehouse.id,
                inventory_item_id=to_item.id,
                transaction_type="TRANSFER_IN",
                quantity=quantity,
                unit_price=from_item.unit_price,
                total_value=quantity * from_item.unit_price,
                balance_before=to_item.physical_qty,
                balance_after=to_item.physical_qty + quantity,
                reference_type="TRANSFER",
                reference_no=transfer_no,
                performed_by=performed_by,
                remarks=f"انتقال از انبار {from_warehouse.name}"
            )
            session.add(in_transaction)

            # به‌روزرسانی موجودی انبار مبدا
            from_item.physical_qty -= quantity
            from_item.available_qty = from_item.physical_qty - from_item.reserved_qty
            from_item.total_value = from_item.physical_qty * from_item.unit_price
            from_item.last_issue_date = datetime.utcnow()

            # به‌روزرسانی موجودی انبار مقصد
            to_item.physical_qty += quantity
            to_item.available_qty = to_item.physical_qty - to_item.reserved_qty
            to_item.total_value = to_item.physical_qty * to_item.unit_price
            to_item.last_receipt_date = datetime.utcnow()

            session.commit()
            session.refresh(out_transaction)
            session.refresh(in_transaction)

            self._log_activity(
                action="WAREHOUSE_TRANSFER",
                details=f"انتقال {quantity} {material_code} از {from_warehouse_code} به {to_warehouse_code}"
            )

            return out_transaction, in_transaction

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    # ================== متدهای کمکی خصوصی ==================

    def _log_activity(self, action: str, details: str):
        """ثبت لاگ فعالیت (اختیاری)"""
        # اگر activity_logger پاس داده شده، از آن استفاده کن
        if self.activity_logger:
            try:
                self.activity_logger(
                    action=action,
                    details=details,
                    user="System"
                )
            except Exception as e:
                print(f"خطا در ثبت لاگ: {e}")
