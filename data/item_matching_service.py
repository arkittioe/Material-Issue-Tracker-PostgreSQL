# file: data/item_matching_service.py
"""
سرویس هوشمند تطبیق آیتم‌ها
- تطبیق بر اساس کد دقیق
- تطبیق بر اساس قوانین
- یادگیری از انتخاب‌های کاربر
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from difflib import SequenceMatcher

from sqlalchemy import func, or_, and_, desc
from sqlalchemy.orm import Session

from models import (
    ItemMapping, MaterialSearchHistory, MaterialSynonym,
    InventoryItem, Warehouse, MTOItem
)


class ItemMatchingService:
    """سرویس تطبیق هوشمند آیتم‌ها"""

    def __init__(self, session_factory, activity_logger=None):
        self.session_factory = session_factory
        self.log_activity = activity_logger

        # کش قوانین تطبیق
        self._mapping_cache = {}
        self._synonym_cache = {}
        self._last_cache_update = None
        self._cache_ttl_minutes = 30

    # ================== تطبیق اصلی ==================

    def find_matching_items(
            self,
            search_query: str,
            size: str = None,
            spec: str = None,
            warehouse_code: str = None,
            project_id: int = None,
            user_id: str = None,
            limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        جستجوی هوشمند آیتم‌های منطبق

        Returns:
            لیست آیتم‌های منطبق به همراه امتیاز تطابق
        """
        session = self.session_factory()
        try:
            results = []

            # 1. تطبیق دقیق (Exact Match)
            exact_matches = self._find_exact_matches(
                session, search_query, size, spec, warehouse_code
            )
            for item in exact_matches:
                item['match_type'] = 'EXACT'
                item['confidence'] = 1.0
                results.append(item)

            # 2. تطبیق بر اساس قوانین ذخیره شده
            if len(results) < limit:
                rule_matches = self._find_rule_based_matches(
                    session, search_query, size, spec, warehouse_code
                )
                for item in rule_matches:
                    if not self._is_duplicate(results, item):
                        item['match_type'] = 'RULE_BASED'
                        results.append(item)

            # 3. تطبیق بر اساس مترادف‌ها
            if len(results) < limit:
                synonym_matches = self._find_synonym_matches(
                    session, search_query, size, spec, warehouse_code
                )
                for item in synonym_matches:
                    if not self._is_duplicate(results, item):
                        item['match_type'] = 'SYNONYM'
                        results.append(item)

            # 4. تطبیق فازی (Fuzzy Match)
            if len(results) < limit:
                fuzzy_matches = self._find_fuzzy_matches(
                    session, search_query, size, spec, warehouse_code,
                    limit - len(results)
                )
                for item in fuzzy_matches:
                    if not self._is_duplicate(results, item):
                        item['match_type'] = 'FUZZY'
                        results.append(item)

            # 5. رتبه‌بندی بر اساس تاریخچه استفاده
            results = self._rank_by_usage_history(
                session, results, project_id, user_id
            )

            # ثبت جستجو
            if user_id:
                self._log_search(
                    session, search_query, size, spec,
                    warehouse_code, project_id, user_id
                )

            return results[:limit]

        finally:
            session.close()

    def _find_exact_matches(
            self, session: Session,
            search_query: str, size: str, spec: str,
            warehouse_code: str
    ) -> List[Dict[str, Any]]:
        """تطبیق دقیق بر اساس کد"""
        query = session.query(InventoryItem)

        # فیلتر انبار
        if warehouse_code:
            warehouse = session.query(Warehouse).filter_by(
                code=warehouse_code
            ).first()
            if warehouse:
                query = query.filter_by(warehouse_id=warehouse.id)

        # تطبیق دقیق کد
        query = query.filter(
            func.upper(InventoryItem.material_code) == func.upper(search_query)
        )

        # فیلتر سایز
        if size:
            query = query.filter(
                func.upper(InventoryItem.size) == func.upper(size)
            )

        items = query.all()
        return [self._item_to_dict(item, 1.0) for item in items]

    def _find_rule_based_matches(
            self, session: Session,
            search_query: str, size: str, spec: str,
            warehouse_code: str
    ) -> List[Dict[str, Any]]:
        """تطبیق بر اساس قوانین ذخیره شده"""

        # بارگذاری قوانین از کش یا دیتابیس
        mappings = self._get_cached_mappings(session)

        # جستجوی قوانین منطبق
        matching_items = []
        search_upper = search_query.upper()

        for mapping in mappings:
            if mapping['source_code'].upper() == search_upper:
                # پیدا کردن آیتم هدف در انبار
                query = session.query(InventoryItem).filter(
                    func.upper(InventoryItem.material_code) ==
                    func.upper(mapping['target_code'])
                )

                if warehouse_code:
                    warehouse = session.query(Warehouse).filter_by(
                        code=warehouse_code
                    ).first()
                    if warehouse:
                        query = query.filter_by(warehouse_id=warehouse.id)

                if size and mapping.get('target_size'):
                    query = query.filter_by(size=mapping['target_size'])

                items = query.all()
                for item in items:
                    matching_items.append(
                        self._item_to_dict(item, mapping['confidence'])
                    )

        return matching_items

    def _find_synonym_matches(
            self, session: Session,
            search_query: str, size: str, spec: str,
            warehouse_code: str
    ) -> List[Dict[str, Any]]:
        """تطبیق بر اساس مترادف‌ها"""

        # یافتن مترادف‌ها
        synonyms = session.query(MaterialSynonym).filter(
            or_(
                func.upper(MaterialSynonym.synonym_code) == func.upper(search_query),
                func.upper(MaterialSynonym.synonym_description).contains(func.upper(search_query))
            ),
            MaterialSynonym.is_verified == True
        ).all()

        matching_items = []
        for syn in synonyms:
            # جستجوی آیتم اصلی در انبار
            query = session.query(InventoryItem).filter(
                func.upper(InventoryItem.material_code) ==
                func.upper(syn.primary_code)
            )

            if warehouse_code:
                warehouse = session.query(Warehouse).filter_by(
                    code=warehouse_code
                ).first()
                if warehouse:
                    query = query.filter_by(warehouse_id=warehouse.id)

            if size:
                query = query.filter_by(size=size)

            items = query.all()
            for item in items:
                matching_items.append(
                    self._item_to_dict(item, syn.confidence_score)
                )

        return matching_items

    def _find_fuzzy_matches(
            self, session: Session,
            search_query: str, size: str, spec: str,
            warehouse_code: str, limit: int
    ) -> List[Dict[str, Any]]:
        """تطبیق فازی برای موارد نزدیک"""

        query = session.query(InventoryItem)

        # فیلتر انبار
        if warehouse_code:
            warehouse = session.query(Warehouse).filter_by(
                code=warehouse_code
            ).first()
            if warehouse:
                query = query.filter_by(warehouse_id=warehouse.id)

        # جستجوی LIKE برای کد و توضیحات
        search_pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                InventoryItem.material_code.ilike(search_pattern),
                InventoryItem.description.ilike(search_pattern)
            )
        )

        if size:
            query = query.filter_by(size=size)

        items = query.limit(limit * 2).all()  # بیشتر بگیر برای امتیازدهی

        # محاسبه امتیاز شباهت
        scored_items = []
        for item in items:
            # محاسبه شباهت با کد
            code_similarity = SequenceMatcher(
                None,
                search_query.upper(),
                (item.material_code or "").upper()
            ).ratio()

            # محاسبه شباهت با توضیحات
            desc_similarity = SequenceMatcher(
                None,
                search_query.upper(),
                (item.description or "").upper()
            ).ratio()


            # امتیاز نهایی
            confidence = max(code_similarity, desc_similarity * 0.8)

            if confidence >= 0.5:  # حداقل امتیاز شباهت
                scored_items.append({
                    'item': item,
                    'confidence': confidence
                })

            # مرتب‌سازی بر اساس امتیاز
        scored_items.sort(key=lambda x: x['confidence'], reverse=True)

        # تبدیل به فرمت خروجی
        results = []
        for scored in scored_items[:limit]:
            item_dict = self._item_to_dict(scored['item'], scored['confidence'])
            results.append(item_dict)

        return results

    def _rank_by_usage_history(
            self, session: Session,
            items: List[Dict[str, Any]],
            project_id: int = None,
            user_id: str = None
    ) -> List[Dict[str, Any]]:
        """رتبه‌بندی نتایج بر اساس تاریخچه استفاده"""

        if not items or not (project_id or user_id):
            return items

        # شمارش استفاده‌های قبلی
        usage_counts = defaultdict(int)

        # جستجوی تاریخچه
        history_query = session.query(
            MaterialSearchHistory.selected_item_code,
            func.count(MaterialSearchHistory.id).label('usage_count')
        )

        if project_id:
            history_query = history_query.filter_by(project_id=project_id)
        if user_id:
            history_query = history_query.filter_by(user_id=user_id)

        # فقط 30 روز اخیر
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        history_query = history_query.filter(
            MaterialSearchHistory.timestamp >= thirty_days_ago
        )

        history = history_query.group_by(
            MaterialSearchHistory.selected_item_code
        ).all()

        for record in history:
            usage_counts[record.selected_item_code] = record.usage_count

        # اضافه کردن امتیاز استفاده
        for item in items:
            code = item.get('material_code', '')
            usage = usage_counts.get(code, 0)

            # ترکیب امتیاز فعلی با امتیاز استفاده
            current_confidence = item.get('confidence', 0.5)
            usage_boost = min(usage * 0.05, 0.3)  # حداکثر 30% افزایش
            item['confidence'] = min(current_confidence + usage_boost, 1.0)
            item['usage_count'] = usage

        # مرتب‌سازی نهایی
        items.sort(key=lambda x: (x['confidence'], x.get('usage_count', 0)), reverse=True)

        return items

        # ================== ثبت و یادگیری ==================

    def record_user_selection(
            self,
            search_query: str,
            selected_item_code: str,
            user_id: str,
            project_id: int = None,
            confidence_adjustment: float = 0.1
    ) -> bool:
        """ثبت انتخاب کاربر برای یادگیری"""

        session = self.session_factory()
        try:
            # ثبت در تاریخچه جستجو
            history = MaterialSearchHistory(
                search_term=search_query,
                selected_item_code=selected_item_code,
                user_id=user_id,
                project_id=project_id,
                user_feedback='SELECTED'
            )
            session.add(history)

            # به‌روزرسانی یا ایجاد قانون تطبیق
            mapping = session.query(ItemMapping).filter_by(
                source_code=search_query,
                target_code=selected_item_code
            ).first()

            if mapping:
                # افزایش امتیاز اطمینان
                mapping.confidence_score = min(
                    mapping.confidence_score + confidence_adjustment, 1.0
                )
                mapping.usage_count += 1
                mapping.last_used = datetime.utcnow()
            else:
                # ایجاد قانون جدید
                mapping = ItemMapping(
                    source_code=search_query,
                    target_code=selected_item_code,
                    mapping_type='USER_LEARNED',
                    confidence_score=0.5,
                    usage_count=1,
                    created_by=user_id
                )
                session.add(mapping)

            session.commit()

            # پاک کردن کش
            self._clear_cache()

            if self.log_activity:
                self.log_activity(
                    user=user_id,
                    action="MATERIAL_SELECTION",
                    details=f"Selected {selected_item_code} for query '{search_query}'"
                )

            return True

        except Exception as e:
            session.rollback()
            logging.error(f"Error recording user selection: {e}")
            return False
        finally:
            session.close()

    def add_synonym(
            self,
            primary_code: str,
            synonym_code: str,
            synonym_description: str = None,
            created_by: str = None,
            auto_verify: bool = False
    ) -> bool:
        """اضافه کردن مترادف جدید"""

        session = self.session_factory()
        try:
            # بررسی عدم تکرار
            existing = session.query(MaterialSynonym).filter_by(
                primary_code=primary_code,
                synonym_code=synonym_code
            ).first()

            if existing:
                return False

            synonym = MaterialSynonym(
                primary_code=primary_code,
                synonym_code=synonym_code,
                synonym_description=synonym_description,
                is_verified=auto_verify,
                confidence_score=1.0 if auto_verify else 0.5,
                created_by=created_by
            )

            session.add(synonym)
            session.commit()

            # پاک کردن کش
            self._clear_cache()

            if self.log_activity:
                self.log_activity(
                    user=created_by or "System",
                    action="ADD_SYNONYM",
                    details=f"Added synonym {synonym_code} for {primary_code}"
                )

            return True

        except Exception as e:
            session.rollback()
            logging.error(f"Error adding synonym: {e}")
            return False
        finally:
            session.close()

    def learn_from_mto_miv_match(
            self,
            mto_item_code: str,
            warehouse_item_code: str,
            confidence: float = 0.7
    ) -> bool:
        """یادگیری از تطابق‌های MTO و انبار"""

        session = self.session_factory()
        try:
            # ایجاد یا به‌روزرسانی قانون
            mapping = session.query(ItemMapping).filter_by(
                source_code=mto_item_code,
                target_code=warehouse_item_code
            ).first()

            if mapping:
                mapping.usage_count += 1
                mapping.confidence_score = min(
                    (mapping.confidence_score + confidence) / 2, 1.0
                )
                mapping.last_used = datetime.utcnow()
            else:
                mapping = ItemMapping(
                    source_code=mto_item_code,
                    target_code=warehouse_item_code,
                    mapping_type='AUTO_LEARNED',
                    confidence_score=confidence,
                    usage_count=1
                )
                session.add(mapping)

            session.commit()

            # پاک کردن کش
            self._clear_cache()

            return True

        except Exception as e:
            session.rollback()
            logging.error(f"Error learning from MTO-MIV match: {e}")
            return False
        finally:
            session.close()

        # ================== متدهای کمکی ==================

    def _item_to_dict(self, item: InventoryItem, confidence: float) -> Dict[str, Any]:
        """تبدیل آیتم موجودی به دیکشنری"""
        return {
            'id': item.id,
            'material_code': item.material_code,
            'description': item.description,
            'size': item.size,
            'specification': item.specification,
            'heat_no': item.heat_no,
            'available_qty': item.available_qty,
            'unit': item.unit,
            'warehouse_id': item.warehouse_id,
            'warehouse': item.warehouse.name if item.warehouse else None,
            'confidence': confidence
        }

    def _is_duplicate(self, results: List[Dict], item: Dict) -> bool:
        """بررسی تکراری بودن آیتم در نتایج"""
        for existing in results:
            if (existing['material_code'] == item['material_code'] and
                    existing.get('size') == item.get('size') and
                    existing.get('heat_no') == item.get('heat_no')):
                return True
        return False

    def _get_cached_mappings(self, session: Session) -> List[Dict]:
        """دریافت قوانین تطبیق از کش یا دیتابیس"""

        # بررسی کش
        now = datetime.utcnow()
        if (self._last_cache_update and
                (now - self._last_cache_update).total_seconds() < self._cache_ttl_minutes * 60):
            return list(self._mapping_cache.values())

        # بارگذاری از دیتابیس
        mappings = session.query(ItemMapping).filter_by(
            is_active=True
        ).order_by(
            desc(ItemMapping.confidence_score)
        ).all()

        # به‌روزرسانی کش
        self._mapping_cache = {}
        for mapping in mappings:
            key = f"{mapping.source_code}_{mapping.target_code}"
            self._mapping_cache[key] = {
                'source_code': mapping.source_code,
                'target_code': mapping.target_code,
                'target_size': mapping.target_size,
                'confidence': mapping.confidence_score,
                'mapping_type': mapping.mapping_type
            }

        self._last_cache_update = now
        return list(self._mapping_cache.values())

    def _clear_cache(self):
        """پاک کردن کش"""
        self._mapping_cache = {}
        self._synonym_cache = {}
        self._last_cache_update = None

    def _log_search(
            self, session: Session,
            search_query: str, size: str, spec: str,
            warehouse_code: str, project_id: int, user_id: str
    ):
        """ثبت جستجو در تاریخچه"""
        try:
            history = MaterialSearchHistory(
                search_term=search_query,
                search_filters={
                    'size': size,
                    'spec': spec,
                    'warehouse_code': warehouse_code
                },
                project_id=project_id,
                user_id=user_id
            )

            session.add(history)
            session.commit()
        except Exception as e:
            logging.error(f"Error logging search: {e}")

        # ================== گزارشات و آنالیز ==================

    def get_matching_statistics(self) -> Dict[str, Any]:
        """دریافت آمار عملکرد سیستم تطبیق"""

        session = self.session_factory()
        try:
            # آمار جستجوها
            total_searches = session.query(func.count(MaterialSearchHistory.id)).scalar()

            # آمار انتخاب‌ها
            selected_searches = session.query(func.count(MaterialSearchHistory.id)).filter(
                MaterialSearchHistory.selected_item_code.isnot(None)
            ).scalar()

            # آمار قوانین
            total_mappings = session.query(func.count(ItemMapping.id)).scalar()
            active_mappings = session.query(func.count(ItemMapping.id)).filter_by(
                is_active=True
            ).scalar()

            # آمار مترادف‌ها
            total_synonyms = session.query(func.count(MaterialSynonym.id)).scalar()
            verified_synonyms = session.query(func.count(MaterialSynonym.id)).filter_by(
                is_verified=True
            ).scalar()

            # محاسبه نرخ موفقیت
            success_rate = (selected_searches / total_searches * 100) if total_searches > 0 else 0

            return {
                'total_searches': total_searches,
                'successful_matches': selected_searches,
                'success_rate': round(success_rate, 2),
                'total_mappings': total_mappings,
                'active_mappings': active_mappings,
                'total_synonyms': total_synonyms,
                'verified_synonyms': verified_synonyms,
                'cache_size': len(self._mapping_cache)
            }

        finally:
            session.close()
