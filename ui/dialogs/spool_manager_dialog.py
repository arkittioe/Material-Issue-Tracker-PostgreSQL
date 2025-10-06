from PyQt6.QtCore import *
from PyQt6.QtCore import QStringListModel
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from PyQt6.QtWidgets import QCompleter
from data_manager_facade import DataManagerFacade as DataManager
from models import *
import os
import pandas as pd
import sys

class SpoolManagerDialog(QDialog):
    def __init__(self, dm: DataManager, parent=None):
        super().__init__(parent)
        self.dm = dm
        self.setWindowTitle("مدیریت اسپول‌ها")
        self.setMinimumSize(1200, 700)
        self.current_spool_id = None
        self.is_new_spool = True

        layout = QVBoxLayout(self)
        top_groupbox = QGroupBox("اطلاعات اسپول")
        top_layout = QHBoxLayout()
        form_layout = QFormLayout()

        self.spool_id_entry = QLineEdit()
        self.spool_id_entry.setPlaceholderText("شناسه اسپول را وارد یا انتخاب کنید...")
        self.location_entry = QLineEdit()
        self.location_entry.setPlaceholderText("محل قرارگیری اسپول...")

        form_layout.addRow("Spool ID:", self.spool_id_entry)
        form_layout.addRow("Location:", self.location_entry)

        self.load_btn = QPushButton("بارگذاری اسپول")
        self.new_btn = QPushButton("ایجاد اسپول جدید")

        top_layout.addLayout(form_layout, stretch=2)
        top_layout.addWidget(self.load_btn)
        top_layout.addWidget(self.new_btn)
        top_groupbox.setLayout(top_layout)
        layout.addWidget(top_groupbox)

        self.setup_spool_id_completer()

        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "Component Type", "Class/Angle", "Bore1", "Bore2",
            "Material", "Schedule", "Thickness", "Length (m)", "Qty Available", "Item Code"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btns_layout = QHBoxLayout()
        self.add_row_btn = QPushButton("➕ افزودن ردیف")
        self.remove_row_btn = QPushButton("➖ حذف ردیف")
        self.export_btn = QPushButton("خروجی اکسل")
        self.save_btn = QPushButton("💾 ذخیره تغییرات")
        self.close_btn = QPushButton("بستن")
        btns_layout.addWidget(self.add_row_btn)
        btns_layout.addWidget(self.remove_row_btn)
        btns_layout.addStretch()
        btns_layout.addWidget(self.export_btn)
        btns_layout.addWidget(self.save_btn)
        btns_layout.addWidget(self.close_btn)
        layout.addLayout(btns_layout)

        self.load_btn.clicked.connect(self.load_spool)
        self.new_btn.clicked.connect(self.new_spool)
        self.add_row_btn.clicked.connect(self.add_row)
        self.remove_row_btn.clicked.connect(self.remove_row)
        self.save_btn.clicked.connect(self.save_changes)
        self.export_btn.clicked.connect(self.handle_export_to_excel)
        self.close_btn.clicked.connect(self.close)

    def setup_spool_id_completer(self):
        """لیست شناسه‌های اسپول را از دیتابیس گرفته و به ورودی اضافه می‌کند."""
        try:
            spool_ids = self.dm.get_all_spool_ids()
            model = QStringListModel()
            model.setStringList(spool_ids)
            completer = QCompleter(model, self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.spool_id_entry.setCompleter(completer)
        except Exception as e:
            print(f"Failed to setup completer: {e}")

    def populate_table(self, items: list[SpoolItem]):
        """جدول را با آیتم‌های یک اسپول پر می‌کند."""
        self.table.setRowCount(0)
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            def to_str(val):
                return str(val) if val is not None else ""

            self.table.setItem(row, 0, QTableWidgetItem(item.component_type or ""))
            # اصلاح: تبدیل class_angle به رشته برای جلوگیری از خطا
            self.table.setItem(row, 1, QTableWidgetItem(to_str(item.class_angle)))
            self.table.setItem(row, 2, QTableWidgetItem(to_str(item.p1_bore)))
            self.table.setItem(row, 3, QTableWidgetItem(to_str(item.p2_bore)))
            self.table.setItem(row, 4, QTableWidgetItem(item.material or ""))
            self.table.setItem(row, 5, QTableWidgetItem(item.schedule or ""))
            self.table.setItem(row, 6, QTableWidgetItem(to_str(item.thickness)))
            self.table.setItem(row, 7, QTableWidgetItem(to_str(item.length)))
            self.table.setItem(row, 8, QTableWidgetItem(to_str(item.qty_available)))
            self.table.setItem(row, 9, QTableWidgetItem(item.item_code or ""))

    def add_row(self):
        self.table.insertRow(self.table.rowCount())

    def remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def load_spool(self):
        """یک اسپول موجود را برای ویرایش بارگذاری می‌کند."""
        spool_id = self.spool_id_entry.text().strip().upper()
        if not spool_id:
            self.show_msg("هشدار", "لطفاً شناسه اسپول را برای بارگذاری وارد کنید.", icon=QMessageBox.Icon.Warning)
            return

        spool = self.dm.get_spool_by_id(spool_id)
        if not spool:
            self.show_msg("خطا", f"اسپولی با شناسه '{spool_id}' یافت نشد.", icon=QMessageBox.Icon.Critical)
            return

        self.current_spool_id = spool.spool_id
        self.spool_id_entry.setText(spool.spool_id)
        self.location_entry.setText(spool.location or "")
        self.populate_table(spool.items)
        self.is_new_spool = False
        self.log_to_console(f"اسپول '{spool_id}' برای ویرایش بارگذاری شد.", "success")

    def new_spool(self):
        """فرم را برای ایجاد یک اسپول جدید آماده می‌کند."""
        self.current_spool_id = None
        next_id = self.dm.generate_next_spool_id()
        self.spool_id_entry.setText(next_id)
        self.location_entry.clear()
        self.table.setRowCount(0)
        self.is_new_spool = True
        self.log_to_console(f"فرم برای ثبت اسپول جدید ({next_id}) آماده است.", "info")

    def save_changes(self):
        """تغییرات جدول و اطلاعات را در دیتابیس ذخیره می‌کند."""
        spool_id = self.spool_id_entry.text().strip().upper()
        if not spool_id:
            self.show_msg("هشدار", "Spool ID الزامی است.", icon=QMessageBox.Icon.Warning)
            return

        try:
            def safe_float(txt):
                if txt is None:
                    return None
                s = str(txt).strip()
                if not s:
                    return None
                try:
                    return round(float(s), 2)
                except (ValueError, TypeError):
                    return None

            items_data = []
            for r in range(self.table.rowCount()):
                def get_item_text(row, col, to_upper=False):
                    item = self.table.item(row, col)
                    text = item.text().strip() if item and item.text() else None
                    if text and to_upper:
                        return text.upper()
                    return text

                row_data = {
                    "component_type": get_item_text(r, 0, to_upper=True),
                    "class_angle": get_item_text(r, 1, to_upper=True),
                    "p1_bore": safe_float(get_item_text(r, 2)),
                    "p2_bore": safe_float(get_item_text(r, 3)),
                    "material": get_item_text(r, 4, to_upper=True),
                    "schedule": get_item_text(r, 5, to_upper=True),
                    "thickness": safe_float(get_item_text(r, 6)),
                    "length": safe_float(get_item_text(r, 7)),
                    "qty_available": safe_float(get_item_text(r, 8)),
                    "item_code": get_item_text(r, 9, to_upper=True)
                }
                if row_data["component_type"]:
                    items_data.append(row_data)

            spool_data = {
                "spool_id": spool_id,
                "location": self.location_entry.text().strip() or None
            }

            if self.is_new_spool:
                success, msg = self.dm.create_spool(spool_data, items_data)
                if success:
                    self.is_new_spool = False
                    self.current_spool_id = spool_id
            else:
                if self.current_spool_id != spool_id:
                    reply = QMessageBox.question(
                        self,
                        'تغییر شناسه اسپول',
                        f"شناسه اسپول از '{self.current_spool_id}' به '{spool_id}' تغییر کرده. آیا یک اسپول جدید با این شناسه ساخته شود؟",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        success, msg = self.dm.create_spool(spool_data, items_data)
                    else:
                        return
                else:
                    success, msg = self.dm.update_spool(self.current_spool_id, spool_data, items_data)

            if success:
                self.show_msg("موفق", msg)
                self.setup_spool_id_completer()
            else:
                self.show_msg("خطا", msg, icon=QMessageBox.Icon.Critical)

        except Exception as e:
            import traceback
            self.show_msg(
                "خطای بحرانی",
                "عملیات ذخیره‌سازی ناموفق بود.",
                detailed=traceback.format_exc(),
                icon=QMessageBox.Icon.Critical
            )

    def handle_export_to_excel(self):
        """داده‌های اسپول را به فایل اکسل صادر می‌کند."""
        try:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "ذخیره فایل اکسل",
                "Spool_Data.xlsx",
                "Excel Files (*.xlsx)"
            )
            if not path:
                return
            ok, message = self.dm.export_spool_data_to_excel(path)
            icon = QMessageBox.Icon.Information if ok else QMessageBox.Icon.Critical
            self.show_msg("خروجی اکسل", message, icon=icon)
        except Exception as e:
            self.show_msg(
                "خطا",
                "Export به اکسل با خطا مواجه شد.",
                detailed=str(e),
                icon=QMessageBox.Icon.Critical
            )

    def show_msg(self, title, text, detailed=None, icon=QMessageBox.Icon.Information):
        """نمایش پیام به کاربر."""
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        if detailed:
            box.setDetailedText(detailed)
        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        box.exec()

    def log_to_console(self, message, level="info"):
        """ثبت پیام در کنسول."""
        if hasattr(self.parent(), 'log_to_console'):
            self.parent().log_to_console(message, level)
        else:
            print(f"[{level.upper()}] {message}")
