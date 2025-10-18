# data/mto_warehouse_coordinator.py

from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from sqlalchemy.orm import Session

# Import models
from models import (
    MTOItem,
    MTOConsumption,
    InventoryItem,
    InventoryTransaction,
    MaterialReservation
)

# Logger
logger = logging.getLogger(__name__)

class MTOWarehouseCoordinator:
    """هماهنگ‌کننده بین MTO و انبار"""

    def __init__(
            self,
            session_factory,
            warehouse_service,
            item_matching_service,
            mto_service,
            log_activity
    ):
        self.session_factory = session_factory
        self.warehouse_service = warehouse_service
        self.item_matching_service = item_matching_service
        self.mto_service = mto_service
        self.log_activity = log_activity

    def find_and_reserve_for_mto(
            self,
            mto_item_id: int,
            warehouse_code: str = None,
            auto_reserve: bool = False
    ) -> Dict[str, Any]:
        """
        جستجو و رزرو خودکار کالا برای MTO
        """
        session = self.session_factory()
        try:
            # پیدا کردن MTO Item
            mto_item = session.query(MTOItem).get(mto_item_id)
            if not mto_item:
                return {
                    'success': False,
                    'message': f'MTO Item {mto_item_id} یافت نشد'
                }

            # جستجوی آیتم‌های مطابق در انبار
            matching_items = self.item_matching_service.find_warehouse_items(
                mto_item_id=mto_item_id,
                warehouse_code=warehouse_code
            )

            if not matching_items:
                return {
                    'success': False,
                    'message': 'آیتم مطابق در انبار یافت نشد',
                    'items': []
                }

            result = {
                'success': True,
                'items': matching_items,
                'mto_item': {
                    'id': mto_item.id,
                    'material_code': mto_item.material_code,
                    'size': mto_item.size,
                    'required_qty': mto_item.qty
                }
            }

            # رزرو خودکار اگر درخواست شده
            if auto_reserve and matching_items:
                best_match = matching_items[0]  # بهترین تطابق
                if best_match['available_qty'] >= mto_item.qty:
                    reservation = self.warehouse_service.reserve_material(
                        warehouse_code=best_match['warehouse_code'],
                        item_code=best_match['item_code'],
                        quantity=mto_item.qty,
                        mto_item_id=mto_item_id,
                        reserved_by="Auto",
                        notes=f"رزرو خودکار برای MTO Line {mto_item.line_no}"
                    )
                    result['reservation_id'] = reservation.id
                    result['reserved_qty'] = mto_item.qty

            return result

        except Exception as e:
            session.rollback()
            return {
                'success': False,
                'message': str(e)
            }
        finally:
            session.close()

    def process_miv_warehouse_consumption(
            self,
            miv_record_id: int,
            consumption_items: List[Dict]
    ) -> tuple[bool, str]:
        """
        پردازش مصرف از انبار برای MIV
        """
        session = self.session_factory()
        try:
            for item in consumption_items:
                # ثبت مصرف
                consumption = MTOConsumption(
                    mto_item_id=item['mto_item_id'],
                    miv_record_id=miv_record_id,
                    inventory_item_id=item.get('inventory_item_id'),
                    used_qty=item['used_qty'],
                    timestamp=datetime.now()
                )
                session.add(consumption)

                # کاهش موجودی انبار
                if item.get('inventory_item_id'):
                    inv_item = session.get(InventoryItem, item['inventory_item_id'])
                    if inv_item:
                        inv_item.available_qty -= item['used_qty']

                        # ثبت تراکنش
                        transaction = InventoryTransaction(
                            warehouse_id=inv_item.warehouse_id,
                            inventory_item_id=inv_item.id,
                            transaction_type="OUT",
                            quantity=item['used_qty'],
                            reference_type="MIV",
                            reference_id=miv_record_id,
                            performed_by="System",
                            timestamp=datetime.now()
                        )
                        session.add(transaction)

            session.commit()
            return True, "مصرف از انبار با موفقیت ثبت شد"

        except Exception as e:
            session.rollback()
            return False, str(e)
        finally:
            session.close()
