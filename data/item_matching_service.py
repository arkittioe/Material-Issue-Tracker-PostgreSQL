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
    InventoryItem, Warehouse, MTOItem, MTOEmbeddingCache
)
from sentence_transformers import SentenceTransformer
import hashlib
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from sqlalchemy import text
from sklearn.metrics.pairwise import cosine_similarity
import json
# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/item_matching.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


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

        # مدل NLP (lazy loading)
        self._nlp_model = None
        self._nlp_model_name = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'

    @property
    def nlp_model(self):
        """بارگذاری تنبل (Lazy Loading) مدل NLP"""
        if self._nlp_model is None:
            logging.info(f"Loading NLP model: {self._nlp_model_name}")
            self._nlp_model = SentenceTransformer(self._nlp_model_name)
        return self._nlp_model

    def _get_or_create_mto_embedding(self, session, text: str):
        """
        دریافت embedding از کش یا تولید جدید
        """
        try:
            # محاسبه هش متن
            text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()

            # جستجو در کش
            cache_entry = session.query(MTOEmbeddingCache).filter(
                MTOEmbeddingCache.mto_text_hash == text_hash,
                MTOEmbeddingCache.expires_at > datetime.utcnow()  # بررسی انقضا
            ).first()

            if cache_entry and cache_entry.embedding_vector:
                return cache_entry.embedding_vector

            # تولید embedding جدید
            from sentence_transformers import SentenceTransformer

            # بارگذاری مدل (یک بار در کل برنامه)
            if not hasattr(self, '_nlp_model'):
                self._nlp_model = SentenceTransformer(
                    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
                )

            embedding = self._nlp_model.encode(text, show_progress_bar=False)
            embedding_list = embedding.tolist()

            # ذخیره در کش
            if cache_entry:
                cache_entry.embedding_vector = embedding_list
                cache_entry.expires_at = datetime.utcnow() + timedelta(days=7)
            else:
                new_cache = MTOEmbeddingCache(
                    mto_text_hash=text_hash,
                    mto_text=text[:1000],  # ذخیره بخشی از متن برای رفرنس
                    embedding_vector=embedding_list,
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(days=7)
                )
                session.add(new_cache)

            session.commit()
            return embedding_list

        except Exception as e:
            self.log_activity("NLP", "ERROR", f"Error generating embedding: {str(e)}")
            return None

    def find_nlp_similarity_match(self, mto_item_description: str, top_n: int = 5, threshold: float = 0.7):
        """
        جستجوی بر اساس شباهت معنایی NLP (بدون pgvector)
        """
        session = self.get_session()
        try:
            # 1. بررسی و تولید embedding برای MTO
            if not mto_item_description or len(mto_item_description.strip()) < 3:
                return []

            # چک کردن کش برای MTO embedding
            mto_embedding = self._get_or_create_mto_embedding(session, mto_item_description)
            if mto_embedding is None:
                return []

            # 2. بارگذاری آیتم‌های انبار که embedding دارند
            warehouse_items = session.query(
                InventoryItem.id,
                InventoryItem.item_code,
                InventoryItem.description,
                InventoryItem.unit,
                InventoryItem.available_qty,
                InventoryItem.embedding_vector
            ).filter(
                InventoryItem.embedding_vector.isnot(None),
                InventoryItem.available_qty > 0  # فقط آیتم‌های موجود
            ).all()

            if not warehouse_items:
                self.log_activity("NLP", "INFO", "No items with embeddings found in warehouse")
                return []

            # 3. محاسبه شباهت کسینوسی
            similarities = []
            mto_embedding_array = np.array(mto_embedding).reshape(1, -1)

            for item in warehouse_items:
                try:
                    # تبدیل JSON به numpy array
                    if isinstance(item.embedding_vector, str):
                        item_embedding = json.loads(item.embedding_vector)
                    else:
                        item_embedding = item.embedding_vector

                    item_embedding_array = np.array(item_embedding).reshape(1, -1)

                    # محاسبه شباهت
                    similarity = cosine_similarity(mto_embedding_array, item_embedding_array)[0][0]

                    if similarity >= threshold:
                        similarities.append({
                            'item_id': item.id,
                            'item_code': item.item_code,
                            'description': item.description,
                            'unit': item.unit,
                            'quantity': float(item.available_qty),
                            'confidence': float(similarity),
                            'source': 'NLP_SIMILARITY'
                        })

                except Exception as e:
                    self.log_activity("NLP", "ERROR", f"Error processing item {item.id}: {str(e)}")
                    continue

            # 4. مرتب‌سازی و برگرداندن نتایج برتر
            similarities.sort(key=lambda x: x['confidence'], reverse=True)
            results = similarities[:top_n]

            # لاگ نتایج
            if results:
                self.log_activity(
                    "NLP",
                    "SUCCESS",
                    f"Found {len(results)} NLP matches for '{mto_item_description[:50]}...'"
                )

            return results

        except Exception as e:
            self.log_activity("NLP", "ERROR", f"Error in NLP similarity search: {str(e)}")
            return []
        finally:
            session.close()

    def get_all_suggestions(
            self,
            mto_item_code: str = None,
            mto_description: str = None,
            mto_size: str = None,
            mto_spec: str = None,
            project_id: int = None,
            user_id: int = None,
            top_n: int = 10,
            min_confidence: float = 0.3
    ) -> List[Dict]:
        """
        دریافت پیشنهادات از تمام لایه‌ها
        """
        session = self.session_factory()
        try:
            all_suggestions = []

            # لاگ ورودی‌ها
            logging.info(f"""
            ========== get_all_suggestions ==========
            mto_item_code: {mto_item_code}
            mto_description: {mto_description}
            mto_size: {mto_size}
            mto_spec: {mto_spec}
            project_id: {project_id}
            ==========================================
            """)

            # 1. تطابق دقیق (Exact Match)
            if mto_item_code:
                logging.info(f"Step 1: Searching for exact match with code: {mto_item_code}")

                exact_items = session.query(InventoryItem).filter(
                    or_(
                        InventoryItem.item_code == mto_item_code,
                        InventoryItem.item_code.ilike(f"%{mto_item_code}%"),
                        InventoryItem.warehouse_item_number == mto_item_code,
                        InventoryItem.warehouse_item_number.ilike(f"%{mto_item_code}%")
                    ),
                    InventoryItem.available_qty > 0
                ).all()

                logging.info(f"Found {len(exact_items)} exact matches")

                for item in exact_items:
                    # محاسبه confidence بر اساس نوع تطابق
                    if item.item_code and item.item_code.strip().upper() == mto_item_code.strip().upper():
                        confidence = 1.0
                        reason = "تطابق کامل کد"
                    elif item.warehouse_item_number and item.warehouse_item_number.strip().upper() == mto_item_code.strip().upper():
                        confidence = 0.95
                        reason = "تطابق کامل شماره انبار"
                    else:
                        confidence = 0.85
                        reason = "تطابق جزئی"

                    suggestion = self._item_to_suggestion(
                        item,
                        confidence=confidence,
                        source='EXACT',
                        reason=reason
                    )
                    all_suggestions.append(suggestion)

            # 2. تطابق قانون‌محور (Rule-Based Match)
            if mto_item_code or mto_description:
                logging.info("Step 2: Searching for rule-based matches...")

                try:
                    mappings_query = session.query(ItemMapping).filter(
                        ItemMapping.is_active == True
                    )

                    # جستجو بر اساس کد MTO
                    if mto_item_code:
                        mappings_query = mappings_query.filter(
                            or_(
                                ItemMapping.source_code == mto_item_code,
                                ItemMapping.source_code.ilike(f"%{mto_item_code}%")
                            )
                        )

                    # جستجو بر اساس توصیف MTO
                    if mto_description:
                        mappings_query = mappings_query.filter(
                            ItemMapping.source_description.ilike(f"%{mto_description}%")
                        )

                    # جستجو بر اساس سایز MTO
                    if mto_size:
                        mappings_query = mappings_query.filter(
                            ItemMapping.source_size == mto_size
                        )

                    mappings = mappings_query.all()
                    logging.info(f"Found {len(mappings)} active mappings")

                    for mapping in mappings:
                        # پیدا کردن آیتم انبار مرتبط با استفاده از target_code
                        inv_item = session.query(InventoryItem).filter(
                            InventoryItem.item_code == mapping.target_code,
                            InventoryItem.available_qty > 0
                        ).first()

                        if inv_item:
                            # محاسبه confidence
                            base_confidence = mapping.confidence_score if mapping.confidence_score else 0.8

                            # افزایش confidence بر اساس تعداد استفاده
                            if mapping.usage_count and mapping.usage_count > 10:
                                base_confidence = min(1.0, base_confidence * 1.1)
                            elif mapping.usage_count and mapping.usage_count > 5:
                                base_confidence = min(1.0, base_confidence * 1.05)

                            # کاهش جزئی برای قانون‌محور نسبت به exact
                            confidence = base_confidence * 0.95

                            # ساخت دلیل
                            reason = f"قانون {mapping.mapping_type}"
                            if mapping.usage_count and mapping.usage_count > 0:
                                reason += f" (استفاده: {mapping.usage_count} بار)"
                            if mapping.notes:
                                reason += f" - {mapping.notes[:50]}"

                            suggestion = self._item_to_suggestion(
                                inv_item,
                                confidence=confidence,
                                source='RULE',
                                reason=reason
                            )
                            all_suggestions.append(suggestion)
                            logging.info(
                                f"Added rule-based suggestion: {inv_item.item_code} (confidence: {confidence:.2f})")

                except Exception as e:
                    logging.error(f"Rule-based matching error: {e}", exc_info=True)

            # 3. جستجو بر اساس توصیف با الگوریتم بهبودیافته
            if mto_description and len(all_suggestions) < 5:
                logging.info(f"Step 3: Searching based on description: {mto_description}")

                # پاکسازی و استخراج کلمات کلیدی
                clean_desc = mto_description.strip().upper()
                # حذف کلمات رایج که ارزش جستجو ندارند
                stop_words = {'AND', 'OR', 'THE', 'FOR', 'WITH', 'IN', 'ON', 'AT', 'TO', 'OF'}
                keywords = [k for k in clean_desc.split() if k not in stop_words and len(k) > 2][:5]

                if keywords:
                    logging.info(f"Keywords extracted: {keywords}")

                    # ساخت شرایط جستجو
                    or_conditions = []
                    for keyword in keywords:
                        or_conditions.extend([
                            InventoryItem.description.ilike(f"%{keyword}%"),
                            InventoryItem.item_code.ilike(f"%{keyword}%"),
                            InventoryItem.warehouse_item_number.ilike(f"%{keyword}%"),
                            InventoryItem.type.ilike(f"%{keyword}%")
                        ])

                    desc_items = session.query(InventoryItem).filter(
                        or_(*or_conditions),
                        InventoryItem.available_qty > 0
                    ).limit(20).all()

                    logging.info(f"Found {len(desc_items)} items by description")

                    for item in desc_items:
                        # محاسبه امتیاز بر اساس تعداد کلمات منطبق
                        item_text = f"{item.description or ''} {item.item_code or ''} {item.type or ''}".upper()
                        matches = sum(1 for k in keywords if k in item_text)

                        if matches > 0:
                            confidence = min(0.7, 0.3 + (matches * 0.15))
                            reason = f'تطابق {matches} از {len(keywords)} کلمه کلیدی'

                            suggestion = self._item_to_suggestion(
                                item,
                                confidence=confidence,
                                source='DESCRIPTION',
                                reason=reason
                            )
                            all_suggestions.append(suggestion)

            # 4. جستجو بر اساس سایز
            if mto_size and len(all_suggestions) < 5:
                logging.info(f"Step 4: Searching based on size: {mto_size}")

                # پاکسازی سایز
                clean_size = str(mto_size).strip().replace('"', '').replace("'", '')

                size_items = session.query(InventoryItem).filter(
                    or_(
                        InventoryItem.size == clean_size,
                        InventoryItem.size.ilike(f"%{clean_size}%"),
                        InventoryItem.description.ilike(f"%{clean_size}%")
                    ),
                    InventoryItem.available_qty > 0
                ).limit(15).all()

                logging.info(f"Found {len(size_items)} items by size")

                for item in size_items:
                    # بررسی تطابق دقیق یا جزئی سایز
                    if item.size and item.size.strip() == clean_size:
                        confidence = 0.6
                        reason = f'سایز دقیق {clean_size}'
                    else:
                        confidence = 0.5
                        reason = f'سایز مشابه {clean_size}'

                    suggestion = self._item_to_suggestion(
                        item,
                        confidence=confidence,
                        source='SIZE',
                        reason=reason
                    )
                    all_suggestions.append(suggestion)

            # 5. پیشنهادات عمومی (اگر نتیجه کافی نداریم)
            if len(all_suggestions) < 3:
                logging.info("Step 5: Adding general inventory items...")

                # آیتم‌های با موجودی بالا
                general_items = session.query(InventoryItem).filter(
                    InventoryItem.available_qty > 0
                ).order_by(
                    InventoryItem.available_qty.desc()
                ).limit(10).all()

                logging.info(f"Found {len(general_items)} general items")

                for item in general_items:
                    # بررسی شباهت کلی
                    confidence = 0.3
                    reason = 'موجودی بالا در انبار'

                    # اگر نوع آیتم مشابه باشد، confidence رو بالا ببر
                    if mto_description and item.type:
                        if item.type.upper() in mto_description.upper():
                            confidence = 0.4
                            reason = f'نوع مشابه ({item.type})'

                    suggestion = self._item_to_suggestion(
                        item,
                        confidence=confidence,
                        source='GENERAL',
                        reason=reason
                    )
                    all_suggestions.append(suggestion)

            # حذف تکراری‌ها و مرتب‌سازی
            unique_suggestions = self._remove_duplicates(all_suggestions)

            # مرتب‌سازی بر اساس confidence
            unique_suggestions.sort(key=lambda x: (x.get('confidence', 0), x.get('available_qty', 0)), reverse=True)

            # اضافه کردن اطلاعات انبار
            for suggestion in unique_suggestions:
                if suggestion.get('warehouse_id'):
                    warehouse = session.query(Warehouse).filter(
                        Warehouse.id == suggestion['warehouse_id']
                    ).first()
                    if warehouse:
                        suggestion['warehouse_code'] = warehouse.code
                        suggestion['warehouse_name'] = warehouse.name

            # فیلتر بر اساس min_confidence
            filtered = [s for s in unique_suggestions if s.get('confidence', 0) >= min_confidence]

            # محدود کردن به top_n
            result = filtered[:top_n] if filtered else unique_suggestions[:top_n]

            logging.info(f"Final result: returning {len(result)} suggestions")

            return result

        except Exception as e:
            logging.error(f"Critical error in get_all_suggestions: {e}", exc_info=True)
            # بازگرداندن لیست خالی در صورت خطا
            return []

        finally:
            session.close()

    def _item_to_suggestion(self, item, confidence: float, source: str, reason: str = '') -> Dict:
        """تبدیل آیتم به فرمت پیشنهاد"""
        try:
            # اگر item یک ORM object است
            if hasattr(item, '__dict__'):
                return {
                    'item_id': getattr(item, 'id', None),
                    'item_code': getattr(item, 'item_code', ''),
                    'warehouse_item_number': getattr(item, 'warehouse_item_number', ''),
                    'description': getattr(item, 'description', ''),
                    'size': getattr(item, 'size', ''),
                    'type': getattr(item, 'type', ''),
                    'unit': getattr(item, 'unit', 'EA'),
                    'available_qty': getattr(item, 'available_qty', 0),
                    'reserved_qty': getattr(item, 'reserved_qty', 0),
                    'warehouse_id': getattr(item, 'warehouse_id', None),
                    'warehouse_code': getattr(item.warehouse, 'code', '') if hasattr(item, 'warehouse') else '',
                    'warehouse_name': getattr(item.warehouse, 'name', '') if hasattr(item, 'warehouse') else '',
                    'confidence': confidence,
                    'match_source': source,
                    'match_reason': reason,
                    'last_transaction_date': getattr(item, 'last_transaction_date', None)
                }
            # اگر item یک dictionary است
            elif isinstance(item, dict):
                result = {
                    'item_id': item.get('id') or item.get('item_id'),
                    'item_code': item.get('item_code', ''),
                    'warehouse_item_number': item.get('warehouse_item_number', ''),
                    'description': item.get('description', ''),
                    'size': item.get('size', ''),
                    'type': item.get('type', ''),
                    'unit': item.get('unit', 'EA'),
                    'available_qty': item.get('available_qty', 0),
                    'reserved_qty': item.get('reserved_qty', 0),
                    'warehouse_id': item.get('warehouse_id'),
                    'warehouse_code': item.get('warehouse_code', ''),
                    'warehouse_name': item.get('warehouse_name', ''),
                    'confidence': confidence,
                    'match_source': source,
                    'match_reason': reason,
                    'last_transaction_date': item.get('last_transaction_date')
                }
                return result
            else:
                # اگر نوع ناشناخته‌ای است
                logging.warning(f"Unknown item type in _item_to_suggestion: {type(item)}")
                return {
                    'item_code': str(item),
                    'description': '',
                    'confidence': confidence,
                    'match_source': source,
                    'match_reason': reason,
                    'available_qty': 0
                }

        except Exception as e:
            logging.error(f"Error in _item_to_suggestion: {e}")
            return {
                'item_code': 'ERROR',
                'description': str(e),
                'confidence': 0,
                'match_source': 'ERROR',
                'available_qty': 0
            }

    def _get_rule_match_reason(self, item: Dict) -> str:
        """
        تولید توضیح برای دلیل تطابق قانون
        """
        reasons = []
        if item.get('mapping_type') == 'MANUAL':
            reasons.append("قانون دستی")
        elif item.get('mapping_type') == 'USER_LEARNED':
            reasons.append("یادگرفته از کاربر")
        elif item.get('mapping_type') == 'AUTO_LEARNED':
            reasons.append("یادگیری خودکار")

        if item.get('usage_count', 0) > 10:
            reasons.append(f"پرکاربرد ({item['usage_count']} بار)")

        return " | ".join(reasons) if reasons else "قانون تطبیق"

    def _get_warehouse_items(self, warehouse_code: Optional[str] = None) -> List[InventoryItem]:
        """دریافت آیتم‌های انبار"""
        session = self.session_factory()
        try:
            query = session.query(InventoryItem).filter(
                InventoryItem.available_qty > 0
            )

            if warehouse_code:
                warehouse = session.query(Warehouse).filter_by(
                    code=warehouse_code,
                    is_active=True
                ).first()
                if warehouse:
                    query = query.filter_by(warehouse_id=warehouse.id)

            return query.all()
        finally:
            session.close()

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

    def _find_exact_matches(self, mto_item: MTOItem, warehouse_items: List[InventoryItem]) -> List[Dict]:
        """جستجوی تطبیق دقیق بر اساس کد آیتم"""
        exact_matches = []

        for inv_item in warehouse_items:
            # تطبیق دقیق کد آیتم
            if inv_item.item_code and mto_item.item_code:
                if inv_item.item_code.strip().upper() == mto_item.item_code.strip().upper():
                    exact_matches.append({
                        'inventory_item': inv_item,
                        'confidence_score': 100,
                        'match_type': 'EXACT_CODE',
                        'match_reason': 'تطبیق دقیق کد آیتم'
                    })
                    continue

            # تطبیق دقیق شماره آیتم انبار
            if hasattr(mto_item, 'warehouse_item_number') and mto_item.warehouse_item_number:
                if inv_item.warehouse_item_number == mto_item.warehouse_item_number:
                    exact_matches.append({
                        'inventory_item': inv_item,
                        'confidence_score': 95,
                        'match_type': 'EXACT_WAREHOUSE_NUM',
                        'match_reason': 'تطبیق دقیق شماره انبار'
                    })

        return exact_matches

    def _find_rule_based_matches(self, mto_item: MTOItem, warehouse_items: List[InventoryItem]) -> List[Dict]:
        """جستجوی تطبیق بر اساس قواعد"""
        rule_matches = []

        for inv_item in warehouse_items:
            confidence = 0
            match_reasons = []

            # Rule 1: تطبیق نوع
            if mto_item.type and inv_item.type:
                if self._match_item_types(mto_item.type, inv_item.type):
                    confidence += 30
                    match_reasons.append("تطبیق نوع")

            # Rule 2: تطبیق کلمات کلیدی در شرح
            if mto_item.description and inv_item.description:
                keyword_score = self._match_description_keywords(
                    mto_item.description,
                    inv_item.description
                )
                if keyword_score > 0:
                    confidence += min(40, keyword_score)
                    match_reasons.append(f"تطبیق کلمات کلیدی ({keyword_score}%)")

            # Rule 3: تطبیق سایز
            if mto_item.size and inv_item.size:
                size_score = self._match_sizes(mto_item.size, inv_item.size)
                if size_score > 0:
                    confidence += min(30, size_score)
                    match_reasons.append(f"تطبیق سایز ({size_score}%)")

            # اگر امتیاز کافی باشد، اضافه کن
            if confidence >= 50:
                rule_matches.append({
                    'inventory_item': inv_item,
                    'confidence_score': min(90, confidence),
                    'match_type': 'RULE_BASED',
                    'match_reason': ' | '.join(match_reasons)
                })

        # مرتب‌سازی بر اساس امتیاز
        rule_matches.sort(key=lambda x: x['confidence_score'], reverse=True)
        return rule_matches[:10]  # حداکثر 10 نتیجه

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
                code=warehouse_code,
                is_active=True
            ).first()
            if warehouse:
                query = query.filter_by(warehouse_id=warehouse.id)

        # جستجوی LIKE برای کد و توضیحات - تغییر به item_code
        search_pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                InventoryItem.item_code.ilike(search_pattern),
                InventoryItem.description.ilike(search_pattern)
            )
        )

        if size:
            query = query.filter_by(size=size)

        # فقط موارد موجود
        query = query.filter(InventoryItem.available_qty > 0)

        items = query.limit(limit * 2).all()

        # محاسبه امتیاز شباهت
        scored_items = []
        for item in items:
            # محاسبه شباهت با کد
            code_similarity = SequenceMatcher(
                None,
                search_query.upper(),
                (item.item_code or "").upper()  # تغییر به item_code
            ).ratio()

            # محاسبه شباهت با توضیحات
            desc_similarity = SequenceMatcher(
                None,
                search_query.upper(),
                (item.description or "").upper()
            ).ratio()

            # امتیاز نهایی
            confidence = max(code_similarity, desc_similarity * 0.8)

            if confidence >= 0.5:
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


    def _match_item_types(self, type1: str, type2: str) -> bool:
        """تطبیق انواع آیتم‌ها با در نظر گرفتن مترادف‌ها"""
        t1 = type1.strip().upper()
        t2 = type2.strip().upper()
        if t1 == t2:
            return True

        synonyms = {
            "PIPE": ["TUBE", "PIPING"],
            "VALVE": ["BALL VALVE", "GATE VALVE", "CONTROL VALVE"],
            "FITTING": ["ELBOW", "TEE", "REDUCER", "CAP"],
            "FLANGE": ["FACING", "RING TYPE"],
        }
        return any(t2 in v for k, v in synonyms.items() if k == t1 or k in t2)

    def _match_description_keywords(self, desc1: str, desc2: str) -> int:
        """مقایسه کلمات کلیدی توضیحات بین MTO و انبار"""
        import re
        words1 = set(re.findall(r'\b\w+\b', desc1.upper()))
        words2 = set(re.findall(r'\b\w+\b', desc2.upper()))
        if not words1:
            return 0
        overlap = words1.intersection(words2)
        return int((len(overlap) / len(words1)) * 100)

    def _match_sizes(self, size1: str, size2: str) -> int:
        """تطبیق سایز با در نظر گرفتن تبدیل واحدهای رایج"""
        import re

        def normalize(s):
            s = s.lower().replace('"', 'in')
            s = re.sub(r'[^\d./a-z]', '', s)
            return s

        s1, s2 = normalize(size1), normalize(size2)
        if s1 == s2:
            return 100
        # تبدیل ساده اینچ و میلی‌متر
        try:
            def to_mm(txt):
                txt = txt.replace('in', '')
                val = float(txt)
                if 'in' in size1:
                    val *= 25.4
                return val

            d1, d2 = to_mm(s1), to_mm(s2)
            diff = abs(d1 - d2)
            return max(0, int(100 - (diff / max(d1, d2)) * 100))
        except Exception:
            return 0

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
            'material_code': item.item_code,  # تغییر نام برای سازگاری
            'description': item.description,
            'size': item.size,
            'type': item.type,  # اضافه شد
            'warehouse_item_number': item.warehouse_item_number,  # اضافه شد
            'available_qty': item.available_qty,
            'reserved_qty': item.reserved_qty,  # اضافه شد
            'unit': item.unit,
            'warehouse_id': item.warehouse_id,
            'warehouse': item.warehouse.name if item.warehouse else None,
            'confidence': confidence,
            'last_transaction_date': item.last_transaction_date  # اضافه شد
        }

    def find_warehouse_items(
            self,
            search_term: str,
            project_id: int = None,
            filters: Dict[str, Any] = None,
            limit: int = 50
    ) -> List[Dict[str, Any]]:
        """جستجوی هوشمند در انبار"""

        session = self.session_factory()
        try:
            results = []
            search_term = search_term.strip().upper() if search_term else ""

            # 1. جستجوی دقیق
            exact_matches = session.query(InventoryItem).join(Warehouse).filter(
                InventoryItem.item_code == search_term,  # تغییر
                InventoryItem.available_qty > 0,
                Warehouse.is_active == True
            ).all()

            for item in exact_matches:
                dict_item = self._item_to_dict(item, confidence=1.0)
                if not self._is_duplicate(results, dict_item):
                    results.append(dict_item)

            # 2. بررسی قوانین تطبیق
            if len(results) < limit:
                mappings = self._get_cached_mappings(session)
                for mapping in mappings:
                    if mapping['source_code'] == search_term:
                        mapped_items = session.query(InventoryItem).join(Warehouse).filter(
                            InventoryItem.item_code == mapping['target_code'],  # تغییر
                            InventoryItem.available_qty > 0,
                            Warehouse.is_active == True
                        )

                        # بررسی سایز هدف اگر در mapping_rules موجود باشد
                        if mapping.get('mapping_rules'):
                            rules = mapping['mapping_rules']
                            if isinstance(rules, dict) and rules.get('target_size'):
                                mapped_items = mapped_items.filter(
                                    InventoryItem.size == rules['target_size']
                                )

                        for item in mapped_items.limit(5).all():
                            dict_item = self._item_to_dict(item, confidence=mapping['confidence'])
                            if not self._is_duplicate(results, dict_item):
                                results.append(dict_item)

            # 3. جستجوی مترادف‌ها
            if len(results) < limit:
                synonyms = session.query(MaterialSynonym).filter(
                    MaterialSynonym.synonym_code == search_term
                ).all()

                for synonym in synonyms:
                    syn_items = session.query(InventoryItem).join(Warehouse).filter(
                        InventoryItem.item_code == synonym.primary_code,  # تغییر
                        InventoryItem.available_qty > 0,
                        Warehouse.is_active == True
                    ).limit(3).all()

                    for item in syn_items:
                        dict_item = self._item_to_dict(item, confidence=synonym.confidence_score)
                        if not self._is_duplicate(results, dict_item):
                            results.append(dict_item)

            # 4. جستجوی فازی
            if len(results) < limit and len(search_term) > 3:
                fuzzy_items = session.query(InventoryItem).join(Warehouse).filter(
                    or_(
                        InventoryItem.item_code.like(f"%{search_term}%"),  # تغییر
                        InventoryItem.description.like(f"%{search_term}%")
                    ),
                    InventoryItem.available_qty > 0,
                    Warehouse.is_active == True
                ).limit(limit - len(results)).all()

                for item in fuzzy_items:
                    dict_item = self._item_to_dict(item, confidence=0.7)
                    if not self._is_duplicate(results, dict_item):
                        results.append(dict_item)

            # اعمال فیلترها
            if filters:
                if 'size' in filters and filters['size']:
                    results = [r for r in results if r.get('size') == filters['size']]

                if 'warehouse_id' in filters and filters['warehouse_id']:
                    results = [r for r in results if r['warehouse_id'] == filters['warehouse_id']]

                if 'min_qty' in filters and filters['min_qty']:
                    results = [r for r in results if r['available_qty'] >= filters['min_qty']]

            # مرتب‌سازی
            results.sort(key=lambda x: x['confidence'], reverse=True)

            # ثبت در تاریخچه
            if search_term and len(results) > 0:
                history = MaterialSearchHistory(
                    search_term=search_term,
                    search_filters=filters,
                    search_context="WAREHOUSE_SEARCH",
                    user_id="System",
                    project_id=project_id,
                    timestamp=datetime.utcnow()
                )
                session.add(history)
                session.commit()

            return results[:limit]

        except Exception as e:
            session.rollback()
            logging.error(f"Error in warehouse search: {e}")
            return []
        finally:
            session.close()

    def _is_duplicate(self, results: List[Dict], item: Dict) -> bool:
        """بررسی تکراری بودن آیتم در نتایج"""
        for existing in results:
            if (existing['material_code'] == item['material_code'] and
                    existing.get('size') == item.get('size')):
                # حذف مقایسه heat_no که دیگر وجود ندارد
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
                'mapping_rules': mapping.mapping_rules,  # به جای target_size
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

    def find_warehouse_items_for_mto(
            self,
            mto_item_id: int,
            warehouse_code: str = None
    ) -> List[Dict[str, Any]]:
        """
        جستجوی آیتم‌های انبار مناسب برای یک MTO Item
        این تابع در واقع wrapper برای find_warehouse_items است
        """
        return self.find_warehouse_items(
            mto_item_id=mto_item_id,
            warehouse_code=warehouse_code
        )

    def reserve_warehouse_item_for_mto(
            self,
            warehouse_code: str,
            item_code: str,
            quantity: float,
            mto_item_id: int,
            reserved_by: str = None
    ) -> tuple[bool, str]:
        """
        رزرو کالا از انبار برای MTO
        این تابع هماهنگی بین warehouse و MTO را انجام می‌دهد
        """
        session = self.session_factory()
        try:
            # پیدا کردن warehouse service instance
            # یا می‌توانیم warehouse_service را به constructor اضافه کنیم
            from data.warehouse_service import WarehouseService
            warehouse_service = WarehouseService(self.session_factory, self.log_activity)

            reservation = warehouse_service.reserve_material(
                warehouse_code=warehouse_code,
                item_code=item_code,
                quantity=quantity,
                mto_item_id=mto_item_id,
                reserved_by=reserved_by,
                notes=f"رزرو برای MTO Item #{mto_item_id}"
            )

            # ثبت در جدول mapping یا history
            self.record_material_selection(
                mto_item_id=mto_item_id,
                selected_item_code=item_code,
                selected_warehouse_id=reservation.inventory_item.warehouse_id,
                quantity=quantity,
                source="RESERVATION"
            )

            return True, f"رزرو با شماره {reservation.id} انجام شد"

        except Exception as e:
            session.rollback()
            return False, str(e)
        finally:
            session.close()

    # در فایل data/item_matching_service.py
    # این متدها را به کلاس ItemMatchingService اضافه کنید:

    def record_user_selection(
            self,
            mto_item_code: str,
            mto_description: str,
            selected_item_code: str,
            selected_item_id: int,
            match_source: str,  # 'EXACT', 'RULE', 'NLP'
            confidence_at_selection: float,
            user_id: str,
            project_id: int = None
    ) -> bool:
        """
        ثبت انتخاب کاربر و یادگیری از آن

        Returns:
            bool: موفقیت عملیات
        """
        session = self.session_factory()
        try:
            # 1. ثبت در تاریخچه جستجو
            history = MaterialSearchHistory(
                search_term=mto_item_code,
                selected_item_code=selected_item_code,
                search_context=f"MIV_{match_source}",
                user_id=user_id,
                project_id=project_id,
                user_feedback='SELECTED',
                search_filters={
                    'mto_description': mto_description,
                    'match_source': match_source,
                    'confidence': confidence_at_selection,
                    'selected_item_id': selected_item_id
                }
            )
            session.add(history)

            # 2. به‌روزرسانی یا ایجاد قانون تطبیق
            existing_mapping = session.query(ItemMapping).filter_by(
                source_code=mto_item_code,
                target_code=selected_item_code,
                is_active=True
            ).first()

            if existing_mapping:
                # افزایش اعتماد قانون موجود
                existing_mapping.usage_count += 1
                existing_mapping.last_used = datetime.utcnow()

                # افزایش تدریجی اعتماد (حداکثر 0.95 برای قوانین یادگرفته شده)
                if existing_mapping.mapping_type in ['USER_LEARNED', 'AUTO_LEARNED']:
                    confidence_boost = 0.02  # 2% افزایش با هر استفاده
                    new_confidence = min(
                        existing_mapping.confidence_score + confidence_boost,
                        0.95
                    )
                    existing_mapping.confidence_score = new_confidence

                logging.info(
                    f"Updated mapping: {mto_item_code} -> {selected_item_code}, "
                    f"confidence: {existing_mapping.confidence_score:.2f}, "
                    f"usage: {existing_mapping.usage_count}"
                )

            else:
                # بررسی آستانه برای ایجاد قانون جدید
                recent_selections = session.query(MaterialSearchHistory).filter(
                    MaterialSearchHistory.search_term == mto_item_code,
                    MaterialSearchHistory.selected_item_code == selected_item_code,
                    MaterialSearchHistory.timestamp >= datetime.utcnow() - timedelta(days=30)
                ).count()

                # اگر حداقل 2 بار در 30 روز اخیر انتخاب شده، قانون ایجاد کن
                if recent_selections >= 2:
                    new_mapping = ItemMapping(
                        source_code=mto_item_code,
                        source_description=mto_description,
                        target_code=selected_item_code,
                        mapping_type='USER_LEARNED',
                        confidence_score=0.7,  # شروع با اعتماد متوسط
                        usage_count=recent_selections,
                        created_by=user_id,
                        last_used=datetime.utcnow(),
                        mapping_rules={
                            'learned_from': 'user_selections',
                            'initial_match_source': match_source,
                            'creation_threshold': recent_selections
                        }
                    )
                    session.add(new_mapping)
                    logging.info(
                        f"Created new USER_LEARNED mapping: "
                        f"{mto_item_code} -> {selected_item_code}"
                    )

            # 3. پاک کردن کش برای بازتاب فوری تغییرات
            self._clear_cache()

            session.commit()

            # 4. ثبت در لاگ فعالیت
            if self.log_activity:
                self.log_activity(
                    user=user_id,
                    action="MATERIAL_SELECTION_LEARNED",
                    details=(
                        f"Selected {selected_item_code} for MTO {mto_item_code} "
                        f"(Source: {match_source}, Confidence: {confidence_at_selection:.2f})"
                    )
                )

            return True

        except Exception as e:
            session.rollback()
            logging.error(f"Error recording user selection: {e}")
            return False
        finally:
            session.close()

    def boost_by_usage_history(
            self,
            results: List[Dict[str, Any]],
            mto_item_code: str,
            project_id: int = None,
            user_id: str = None,
            boost_cross_project: bool = True
    ) -> List[Dict[str, Any]]:
        """
        افزایش امتیاز نتایج بر اساس تاریخچه استفاده

        Args:
            results: لیست نتایج اولیه
            mto_item_code: کد آیتم MTO
            project_id: محدود به پروژه خاص
            user_id: محدود به کاربر خاص
            boost_cross_project: آیا تاریخچه سایر پروژه‌ها هم لحاظ شود

        Returns:
            لیست نتایج با امتیاز به‌روز شده
        """
        if not results:
            return results

        session = self.session_factory()
        try:
            # جمع‌آوری آمار استفاده
            usage_stats = {}

            # Query برای تاریخچه انتخاب‌ها
            base_query = session.query(
                MaterialSearchHistory.selected_item_code,
                func.count(MaterialSearchHistory.id).label('total_usage'),
                func.max(MaterialSearchHistory.timestamp).label('last_used')
            ).filter(
                MaterialSearchHistory.search_term == mto_item_code,
                MaterialSearchHistory.selected_item_code.isnot(None),
                MaterialSearchHistory.timestamp >= datetime.utcnow() - timedelta(days=90)
            )

            # فیلتر پروژه (اختیاری)
            if project_id and not boost_cross_project:
                base_query = base_query.filter(
                    MaterialSearchHistory.project_id == project_id
                )

            # فیلتر کاربر (اختیاری)
            if user_id:
                # اولویت به انتخاب‌های خود کاربر
                user_query = base_query.filter(
                    MaterialSearchHistory.user_id == user_id
                )
                user_history = user_query.group_by(
                    MaterialSearchHistory.selected_item_code
                ).all()

                for record in user_history:
                    usage_stats[record.selected_item_code] = {
                        'user_usage': record.total_usage,
                        'total_usage': 0,
                        'last_used': record.last_used,
                        'boost_factor': 1.0
                    }

            # تاریخچه کلی
            general_history = base_query.group_by(
                MaterialSearchHistory.selected_item_code
            ).all()

            for record in general_history:
                if record.selected_item_code in usage_stats:
                    usage_stats[record.selected_item_code]['total_usage'] = record.total_usage
                else:
                    usage_stats[record.selected_item_code] = {
                        'user_usage': 0,
                        'total_usage': record.total_usage,
                        'last_used': record.last_used,
                        'boost_factor': 1.0
                    }

            # محاسبه ضریب boost
            for item_code, stats in usage_stats.items():
                boost = 1.0

                # Boost بر اساس استفاده شخصی کاربر
                if stats['user_usage'] > 0:
                    if stats['user_usage'] >= 5:
                        boost *= 1.25  # 25% برای استفاده‌های مکرر کاربر
                    elif stats['user_usage'] >= 2:
                        boost *= 1.15  # 15% برای استفاده‌های متوسط
                    else:
                        boost *= 1.05  # 5% برای حداقل استفاده

                # Boost بر اساس استفاده کلی
                if stats['total_usage'] >= 10:
                    boost *= 1.15  # 15% برای آیتم‌های پرکاربرد
                elif stats['total_usage'] >= 5:
                    boost *= 1.08  # 8% برای آیتم‌های متوسط

                # Boost بر اساس تازگی استفاده
                if stats['last_used']:
                    days_since_use = (datetime.utcnow() - stats['last_used']).days
                    if days_since_use <= 7:
                        boost *= 1.1  # 10% برای استفاده‌های اخیر
                    elif days_since_use <= 30:
                        boost *= 1.05  # 5% برای استفاده‌های نسبتاً اخیر

                stats['boost_factor'] = min(boost, 1.5)  # حداکثر 50% boost کلی

            # اعمال boost به نتایج
            for result in results:
                item_code = result.get('material_code', '')
                if item_code in usage_stats:
                    stats = usage_stats[item_code]

                    # اعمال ضریب boost
                    original_confidence = result.get('confidence', 0.5)
                    boosted_confidence = min(
                        original_confidence * stats['boost_factor'],
                        0.99  # حداکثر امتیاز نهایی
                    )

                    # به‌روزرسانی نتیجه
                    result['confidence'] = boosted_confidence
                    result['usage_boost_applied'] = True
                    result['usage_stats'] = {
                        'personal_usage': stats['user_usage'],
                        'total_usage': stats['total_usage'],
                        'boost_factor': stats['boost_factor'],
                        'last_used': stats['last_used'].isoformat() if stats['last_used'] else None
                    }

            # مرتب‌سازی مجدد بر اساس امتیاز جدید
            results.sort(
                key=lambda x: (
                    x.get('confidence', 0),
                    x.get('usage_stats', {}).get('total_usage', 0)
                ),
                reverse=True
            )

            return results

        except Exception as e:
            logging.error(f"Error applying usage history boost: {e}")
            return results
        finally:
            session.close()

    def cleanup_old_mappings(self, days_inactive: int = 180) -> int:
        """
        حذف یا کاهش اعتماد قوانین قدیمی و غیرفعال

        Args:
            days_inactive: تعداد روزهای عدم استفاده برای کاهش اعتماد

        Returns:
            تعداد قوانین به‌روزرسانی شده
        """
        session = self.session_factory()
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_inactive)
            updated_count = 0

            # یافتن قوانین قدیمی
            old_mappings = session.query(ItemMapping).filter(
                ItemMapping.mapping_type.in_(['USER_LEARNED', 'AUTO_LEARNED']),
                ItemMapping.last_used < cutoff_date,
                ItemMapping.is_active == True
            ).all()

            for mapping in old_mappings:
                days_old = (datetime.utcnow() - mapping.last_used).days

                if days_old > 365:
                    # غیرفعال کردن قوانین خیلی قدیمی
                    mapping.is_active = False
                    logging.info(f"Deactivated old mapping: {mapping.source_code} -> {mapping.target_code}")
                else:
                    # کاهش تدریجی اعتماد
                    reduction_factor = 0.9 ** (days_old / 180)  # هر 180 روز 10% کاهش
                    mapping.confidence_score *= reduction_factor
                    mapping.confidence_score = max(mapping.confidence_score, 0.3)  # حداقل 30%

                updated_count += 1

            session.commit()
            self._clear_cache()

            logging.info(f"Cleaned up {updated_count} old mappings")
            return updated_count

        except Exception as e:
            session.rollback()
            logging.error(f"Error cleaning up old mappings: {e}")
            return 0
        finally:
            session.close()

    def get_session(self):
        """دریافت session جدید از factory"""
        return self.session_factory()

    def find_nlp_similarity_matches(self, search_text: str, warehouse_code: str = None,
                                    min_similarity: float = 0.6, top_k: int = 5) -> List[Dict]:
        """
        Wrapper برای سازگاری با کد قدیمی
        """
        results = self.find_nlp_similarity_match(
            mto_item_description=search_text,
            top_n=top_k,
            threshold=min_similarity
        )

        # تبدیل فرمت خروجی برای سازگاری
        formatted_results = []
        for item in results:
            formatted_results.append({
                'inventory_item': {
                    'id': item['item_id'],
                    'item_code': item['item_code'],
                    'description': item['description'],
                    'size': item.get('size'),
                    'type': item.get('type'),
                    'warehouse_id': item.get('warehouse_id'),
                    'warehouse_code': warehouse_code or '',
                    'warehouse_name': '',
                    'available_qty': item.get('quantity', 0),
                    'unit': item.get('unit', 'EA')
                },
                'similarity': item.get('confidence', min_similarity)
            })

        return formatted_results

    def _remove_duplicates(self, suggestions: List[Dict]) -> List[Dict]:
        """حذف پیشنهادات تکراری بر اساس item_id"""
        seen = {}
        unique = []

        for suggestion in suggestions:
            item_id = suggestion.get('item_id')
            if item_id:
                if item_id not in seen:
                    seen[item_id] = suggestion
                    unique.append(suggestion)
                else:
                    # اگر confidence جدید بیشتر است، جایگزین کن
                    if suggestion.get('confidence', 0) > seen[item_id].get('confidence', 0):
                        # پیدا کردن و جایگزینی
                        for i, s in enumerate(unique):
                            if s.get('item_id') == item_id:
                                unique[i] = suggestion
                                seen[item_id] = suggestion
                                break
            else:
                # اگر item_id نداره، بر اساس item_code چک کن
                item_code = suggestion.get('item_code')
                if item_code and item_code not in [s.get('item_code') for s in unique]:
                    unique.append(suggestion)

        return unique
