# file: models.py

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, ForeignKey, UniqueConstraint, Index, Text, JSON
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timedelta
from sqlalchemy import Column, Text, DateTime
import hashlib

Base = declarative_base()
# -------------------------
# جدول پروژه‌ها
# -------------------------
class Project(Base):
    __tablename__ = 'projects'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    miv_records = relationship("MIVRecord", back_populates="project")
    mto_items = relationship("MTOItem", back_populates="project")


# -------------------------
# جدول MIV Records
# -------------------------
class MIVRecord(Base):
    __tablename__ = 'miv_records'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    line_no = Column(String, nullable=False)
    miv_tag = Column(String, unique=True)
    location = Column(String)
    status = Column(String)
    comment = Column(String)
    registered_for = Column(String)
    registered_by = Column(String)
    last_updated = Column(DateTime, default=datetime.utcnow)  # همه رکوردهای جدید تاریخ امروز
    is_complete = Column(Boolean, default=False)

    project = relationship("Project", back_populates="miv_records")

    # <<< ADDED: ایندکس ترکیبی برای جستجوهای متداول
    __table_args__ = (
        Index('ix_miv_records_project_line', 'project_id', 'line_no'),
    )

# -------------------------
# جدول MTO Items
# -------------------------
class MTOItem(Base):
    __tablename__ = 'mto_items'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    unit = Column(String)            # UNIT
    line_no = Column(String, nullable=False)
    item_class = Column(String)      # Class
    item_type = Column(String)       # Type
    description = Column(String)
    item_code = Column(String)
    material_code = Column(String)   # Mat.
    p1_bore_in = Column(Float)
    p2_bore_in = Column(Float)
    p3_bore_in = Column(Float)
    length_m = Column(Float)
    quantity = Column(Float)
    joint = Column(Float)
    inch_dia = Column(Float)

    project = relationship("Project", back_populates="mto_items")

    # <<< ADDED: ایندکس ترکیبی برای جستجوهای متداول
    __table_args__ = (
        Index('ix_mto_items_project_line', 'project_id', 'line_no'),
    )
# -------------------------
# جدول MTO Progress
# -------------------------
class MTOProgress(Base):
    __tablename__ = 'mto_progress'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    line_no = Column(String, nullable=False)
    mto_item_id = Column(Integer, ForeignKey('mto_items.id'), nullable=False)  # 🔹 اضافه شد
    item_code = Column(String)
    description = Column(String)
    unit = Column(String)
    total_qty = Column(Float)
    used_qty = Column(Float)
    remaining_qty = Column(Float)
    last_updated = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('project_id', 'line_no', 'item_code', 'mto_item_id', name='uq_progress_item'),  # ✅ کلید یکتا
    )



# -------------------------
# جدول MTO Consumption
# -------------------------
class MTOConsumption(Base):
    __tablename__ = 'mto_consumption'
    id = Column(Integer, primary_key=True)
    mto_item_id = Column(Integer, ForeignKey('mto_items.id'), nullable=False)
    miv_record_id = Column(Integer, ForeignKey('miv_records.id'), nullable=False)
    # 🆕 فیلد جدید برای مصرف از انبار عمومی
    inventory_item_id = Column(Integer, ForeignKey('inventory_items.id'), nullable=True)

    used_qty = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # 🆕 رابطه جدید
    inventory_item = relationship("InventoryItem", backref="consumptions")

# -------------------------
# جدول Activity Log
# -------------------------
class ActivityLog(Base):
    __tablename__ = 'activity_logs'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user = Column(String)
    action = Column(String)
    details = Column(String)


# -------------------------
# جدول Migrated Files
# -------------------------
class MigratedFile(Base):
    __tablename__ = 'migrated_files'
    id = Column(Integer, primary_key=True)
    filename = Column(String, unique=True, nullable=False)
    migrated_at = Column(DateTime, default=datetime.utcnow)



# -------------------------
# جدول Spools
# -------------------------
class Spool(Base):
    __tablename__ = 'spools'
    id = Column(Integer, primary_key=True)
    spool_id = Column(String, unique=True, nullable=False)  # این همان SPOOL_ID در فایل CSV است
    row_no = Column(Integer)
    line_no = Column(String)
    sheet_no = Column(Integer)
    location = Column(String)
    command = Column(String)

    # تعریف رابطه: هر اسپول می‌تواند چندین آیتم داشته باشد
    items = relationship("SpoolItem", back_populates="spool", cascade="all, delete-orphan")
    # تعریف رابطه: هر اسپول می‌تواند در چندین رکورد مصرف ثبت شود
    consumptions = relationship("SpoolConsumption", back_populates="spool", cascade="all, delete-orphan")


# -------------------------
# جدول SpoolItems
# -------------------------
class SpoolItem(Base):
    __tablename__ = 'spool_items'
    id = Column(Integer, primary_key=True)
    # کلید خارجی برای اتصال به جدول Spool
    spool_id_fk = Column(Integer, ForeignKey('spools.id'), nullable=False)

    component_type = Column(String)
    class_angle = Column(Float)
    p1_bore = Column(Float)
    p2_bore = Column(Float)
    material = Column(String)
    schedule = Column(String)
    thickness = Column(Float)
    length = Column(Float)
    qty_available = Column(Float)
    item_code = Column(String)

    # تعریف رابطه: هر آیتم متعلق به یک اسپول است
    spool = relationship("Spool", back_populates="items")
    # تعریف رابطه: هر آیتم اسپول می‌تواند در چندین رکورد مصرف ثبت شود
    consumptions = relationship("SpoolConsumption", back_populates="spool_item", cascade="all, delete-orphan")


# -------------------------
# جدول SpoolConsumption (این جدول از روی فایل ساخته نمی‌شود ولی ساختار آن لازم است)
# -------------------------
class SpoolConsumption(Base):
    __tablename__ = 'spool_consumption'
    id = Column(Integer, primary_key=True)

    # کلیدهای خارجی برای اتصال به جداول دیگر
    spool_item_id = Column(Integer, ForeignKey('spool_items.id'), nullable=False)
    spool_id = Column(Integer, ForeignKey('spools.id'), nullable=False)
    miv_record_id = Column(Integer, ForeignKey('miv_records.id'), nullable=False)

    used_qty = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # تعریف روابط
    spool_item = relationship("SpoolItem", back_populates="consumptions")
    spool = relationship("Spool", back_populates="consumptions")

class SpoolProgress(Base):
    __tablename__ = "spool_progress"

    id = Column(Integer, primary_key=True)
    spool_item_id = Column(Integer, ForeignKey("spool_items.id"))   # آیتم اسپول
    spool_id = Column(Integer, ForeignKey("spools.id"))             # شماره اسپول
    project_id = Column(Integer, ForeignKey("projects.id"))
    line_no = Column(String)                                        # شماره خط آیتم MTO
    item_code = Column(String)                                      # آیتم کد MTO که مصرف کرده

    used_qty = Column(Float, default=0)                             # مصرف شده برای اون آیتم
    remaining_qty = Column(Float, default=0)                        # باقی‌مانده اسپول
    timestamp = Column(DateTime, default=datetime.now)

# -------------------------
# جدول ایندکس فایل‌های ISO (برای کش)
# -------------------------
class IsoFileIndex(Base):
    __tablename__ = 'iso_file_index'
    id = Column(Integer, primary_key=True)
    file_path = Column(String, unique=True, nullable=False)
    normalized_name = Column(String, index=True) # ایندکس برای جستجوی سریع
    prefix_key = Column(String, index=True) # ایندکس برای جستجوی سریع
    last_modified = Column(DateTime)

    # ===============================================
    # جداول سیستم انبار (Warehouse Management)
    # ===============================================

class Warehouse(Base):
    """جدول انبارها"""
    __tablename__ = 'warehouses'

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    location = Column(String(500))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    inventory_items = relationship("InventoryItem", back_populates="warehouse")
    transactions = relationship("InventoryTransaction", back_populates="warehouse")


class InventoryItem(Base):
    """جدول موجودی کالاها در انبار"""
    __tablename__ = 'inventory_items'

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey('warehouses.id'), nullable=False)

    # مشخصات کالا - بروزرسانی شده
    item_code = Column(String(100), nullable=False)
    warehouse_item_number = Column(String(50))
    type = Column(String(50), nullable=False)  # نوع آیتم (PIPE, FITTING, VALVE, etc.)
    description = Column(String(500))
    size = Column(String(100))
    # حذف شده: specification, heat_no

    # موجودی
    physical_qty = Column(Float, default=0)  # موجودی فیزیکی
    reserved_qty = Column(Float, default=0)  # رزرو شده
    available_qty = Column(Float, default=0)  # قابل تخصیص = physical - reserved

    # اطلاعات مالی و واحد
    unit = Column(String(50), default='EA')
    unit_price = Column(Float, default=0)
    total_value = Column(Float, default=0)

    # آستانه‌های موجودی
    min_stock_level = Column(Float, default=0)
    max_stock_level = Column(Float)
    reorder_point = Column(Float)

    # تاریخ‌ها
    last_receipt_date = Column(DateTime)
    last_issue_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    warehouse = relationship("Warehouse", back_populates="inventory_items")
    transactions = relationship("InventoryTransaction", back_populates="inventory_item")
    reservations = relationship("MaterialReservation", back_populates="inventory_item")
    # رابطه اختیاری با MTOConsumption اگر نیاز باشد
    # mto_consumptions = relationship("MTOConsumption", back_populates="inventory_item")

    # Indexes - بروزرسانی شده
    __table_args__ = (
        Index('ix_inventory_warehouse_item', 'warehouse_id', 'item_code'),
        Index('ix_inventory_type', 'type'),
        UniqueConstraint('warehouse_id', 'item_code', 'size', 'type',
                         name='uq_inventory_item'),
    )

    # فیلدهای جدید برای NLP
    embedding_vector = Column(JSON, nullable=True)
    embedding_text = Column(Text, nullable=True)
    embedding_updated_at = Column(DateTime, nullable=True)

    def get_embedding_text(self) -> str:
        """تولید متن برای embedding"""
        parts = []

        # ترکیب فیلدهای مهم با وزن‌دهی
        if self.item_code:
            parts.append(f"کد: {self.item_code}")
            parts.append(self.item_code)  # تکرار برای اهمیت بیشتر

        if self.warehouse_item_number:
            parts.append(f"شماره: {self.warehouse_item_number}")

        if self.description:
            parts.append(self.description)

        if self.size:
            parts.append(f"سایز: {self.size}")

        if self.type:
            parts.append(f"نوع: {self.type}")

        return " | ".join(parts)

class InventoryTransaction(Base):
    """جدول تراکنش‌های انبار"""
    __tablename__ = 'inventory_transactions'

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey('warehouses.id'), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey('inventory_items.id'), nullable=False)

    # نوع تراکنش
    transaction_type = Column(String(50), nullable=False)  # IN, OUT, ADJUST, RETURN
    transaction_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    # مقادیر
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, default=0)
    total_value = Column(Float, default=0)

    # موجودی‌ها بعد از تراکنش
    balance_before = Column(Float)
    balance_after = Column(Float)

    # مرجع
    reference_type = Column(String(50))  # MIV, PO, ADJUSTMENT, etc.
    reference_id = Column(Integer)
    reference_no = Column(String(100))

    # اطلاعات تکمیلی
    remarks = Column(String(500))
    performed_by = Column(String(100))
    approved_by = Column(String(100))

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relations
    warehouse = relationship("Warehouse", back_populates="transactions")
    inventory_item = relationship("InventoryItem", back_populates="transactions")

    # Indexes
    __table_args__ = (
        Index('ix_transaction_date', 'transaction_date'),
        Index('ix_transaction_reference', 'reference_type', 'reference_id'),
    )


class MaterialReservation(Base):
    """جدول رزرو مواد"""
    __tablename__ = 'material_reservations'

    id = Column(Integer, primary_key=True)
    inventory_item_id = Column(Integer, ForeignKey('inventory_items.id'), nullable=False)

    # اطلاعات رزرو
    reservation_no = Column(String(50), unique=True, nullable=False)
    reserved_qty = Column(Float, nullable=False)
    consumed_qty = Column(Float, default=0)
    remaining_qty = Column(Float)

    # مرجع رزرو
    project_id = Column(Integer, ForeignKey('projects.id'))
    miv_record_id = Column(Integer, ForeignKey('miv_records.id'))
    line_no = Column(String(100))

    # وضعیت و تاریخ
    status = Column(String(50), default='ACTIVE')  # ACTIVE, CONSUMED, CANCELLED
    reservation_date = Column(DateTime, default=datetime.utcnow)
    expiry_date = Column(DateTime)

    # اطلاعات تکمیلی
    reserved_by = Column(String(100))
    approved_by = Column(String(100))
    remarks = Column(String(500))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    inventory_item = relationship("InventoryItem", back_populates="reservations")

    # Indexes
    __table_args__ = (
        Index('ix_reservation_status', 'status'),
        Index('ix_reservation_project', 'project_id', 'line_no'),
    )


class InventoryAdjustment(Base):
    """جدول تعدیلات موجودی"""
    __tablename__ = 'inventory_adjustments'

    id = Column(Integer, primary_key=True)
    inventory_item_id = Column(Integer, ForeignKey('inventory_items.id'), nullable=False)

    # نوع و مقدار تعدیل
    adjustment_type = Column(String(50), nullable=False)  # PHYSICAL_COUNT, CORRECTION, DAMAGE
    adjustment_date = Column(DateTime, default=datetime.utcnow)
    quantity_before = Column(Float, nullable=False)
    quantity_after = Column(Float, nullable=False)
    quantity_adjusted = Column(Float, nullable=False)

    # دلیل و توضیحات
    reason = Column(String(500), nullable=False)
    reference_document = Column(String(200))

    # انجام‌دهنده و تأییدکننده
    performed_by = Column(String(100), nullable=False)
    approved_by = Column(String(100))
    approval_date = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Indexes
    __table_args__ = (
        Index('ix_adjustment_date', 'adjustment_date'),
        Index('ix_adjustment_type', 'adjustment_type'),
    )


# ================== مدل‌های تطبیق هوشمند متریال ==================


class ItemMapping(Base):
    """جدول ذخیره قوانین و تطبیقات آیتم‌ها"""
    __tablename__ = 'item_mappings'

    id = Column(Integer, primary_key=True)

    # کد/شرح اصلی (از MTO)
    source_code = Column(String(100), nullable=False, index=True)
    source_description = Column(Text)
    source_size = Column(String(50))
    # حذف شده: source_spec

    # کد/شرح تطبیق یافته (در انبار)
    target_code = Column(String(100), nullable=False, index=True)
    target_description = Column(Text)
    target_size = Column(String(50))
    # حذف شده: target_spec

    # نوع و قدرت تطبیق
    mapping_type = Column(String(50), default='MANUAL')  # MANUAL, RULE_BASED, ML_SUGGESTED, USER_CONFIRMED
    confidence_score = Column(Float, default=1.0)  # 0.0 to 1.0

    # قوانین تطبیق
    mapping_rules = Column(JSON)  # ذخیره قوانین به صورت JSON

    # آمار استفاده
    usage_count = Column(Integer, default=0)
    last_used = Column(DateTime)

    # متادیتا
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    notes = Column(Text)

    # این جدول رابطه‌ای با جداول دیگر ندارد

    # ایندکس‌ها - بروزرسانی شده
    __table_args__ = (
        Index('idx_mapping_source', 'source_code', 'source_size'),
        Index('idx_mapping_target', 'target_code', 'target_size'),
        Index('idx_mapping_active_type', 'is_active', 'mapping_type'),
        UniqueConstraint('source_code', 'source_size', 'target_code', 'target_size',
                         name='uq_source_target_mapping'),
    )


class MaterialSearchHistory(Base):
    """تاریخچه جستجوهای کاربران برای یادگیری"""
    __tablename__ = 'material_search_history'

    id = Column(Integer, primary_key=True)

    # اطلاعات جستجو
    search_term = Column(String(200), nullable=False)
    search_filters = Column(JSON)  # فیلترهای اعمال شده
    search_context = Column(String(100))  # MIV, REPORT, etc.

    # نتیجه انتخاب شده
    selected_item_code = Column(String(100))
    selected_item_description = Column(Text)
    selected_warehouse_id = Column(Integer, ForeignKey('warehouses.id'))

    # اطلاعات کاربر و زمان
    user_id = Column(String(100), nullable=False)
    project_id = Column(Integer, ForeignKey('projects.id'))
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    # آیا انتخاب موفق بود؟
    was_successful = Column(Boolean, default=True)
    user_feedback = Column(String(50))  # CORRECT, WRONG, PARTIAL

    # روابط
    warehouse = relationship("Warehouse", backref="search_histories")
    project = relationship("Project", backref="search_histories")

    __table_args__ = (
        Index('idx_search_timestamp', 'timestamp'),
        Index('idx_search_user_project', 'user_id', 'project_id'),
        Index('idx_search_term', 'search_term'),
    )


class MaterialSynonym(Base):
    """مترادف‌ها و نام‌های جایگزین متریال‌ها"""
    __tablename__ = 'material_synonyms'

    id = Column(Integer, primary_key=True)

    # کد اصلی
    primary_code = Column(String(100), nullable=False, index=True)
    primary_description = Column(Text)

    # مترادف
    synonym_code = Column(String(100))
    synonym_description = Column(Text)
    synonym_type = Column(String(50))  # ABBREVIATION, ALTERNATE_NAME, OLD_CODE, etc.

    # اعتبار
    is_verified = Column(Boolean, default=False)
    confidence_score = Column(Float, default=0.5)

    # متادیتا
    source = Column(String(100))  # MANUAL, IMPORTED, LEARNED
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100))

    __table_args__ = (
        Index('idx_synonym_primary', 'primary_code'),
        Index('idx_synonym_alternate', 'synonym_code'),
        UniqueConstraint('primary_code', 'synonym_code', name='uq_primary_synonym'),
    )


class WarehouseStockSnapshot(Base):
    """عکس لحظه‌ای از وضعیت انبار برای گزارش‌گیری و تحلیل"""
    __tablename__ = 'warehouse_stock_snapshots'

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey('warehouses.id'), nullable=False)
    snapshot_date = Column(DateTime, nullable=False)

    # آمار کلی
    total_items = Column(Integer)
    total_value = Column(Float)
    total_reserved = Column(Float)

    # جزئیات به صورت JSON
    stock_details = Column(JSON)  # لیست آیتم‌ها با موجودی
    low_stock_items = Column(JSON)  # آیتم‌های زیر حد مجاز
    high_turnover_items = Column(JSON)  # آیتم‌های با گردش بالا

    # متادیتا
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(100))

    # روابط
    warehouse = relationship("Warehouse", backref="stock_snapshots")

    __table_args__ = (
        Index('idx_snapshot_warehouse_date', 'warehouse_id', 'snapshot_date'),
        UniqueConstraint('warehouse_id', 'snapshot_date', name='uq_warehouse_snapshot_date'),
    )


class MTOEmbeddingCache(Base):
    """کش برای ذخیره embedding های MTO"""
    __tablename__ = 'mto_embedding_cache'

    id = Column(Integer, primary_key=True)
    mto_text = Column(Text, nullable=False, unique=True)
    mto_text_hash = Column(String(64), nullable=False, unique=True, index=True)
    embedding_vector = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
    hit_count = Column(Integer, default=0)

    def __init__(self, mto_text, embedding_vector=None):
        self.mto_text = mto_text
        self.mto_text_hash = hashlib.sha256(mto_text.encode()).hexdigest()
        self.embedding_vector = embedding_vector
        self.expires_at = datetime.utcnow() + timedelta(days=30)
