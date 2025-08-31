# file: models.py

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
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
    used_qty = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


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