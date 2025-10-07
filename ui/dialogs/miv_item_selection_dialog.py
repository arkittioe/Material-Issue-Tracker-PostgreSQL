# ui/dialogs/miv_item_selection_dialog.py
"""
دیالوگ انتخاب هوشمند آیتم از انبار برای MIV
"""

from typing import Optional, List, Dict, Any
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QGroupBox, QSplitter, QTextEdit, QMessageBox,
    QAbstractItemView, QSpinBox, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont
from data.warehouse_service import WarehouseService
from data.item_matching_service import ItemMatchingService
from models import MTOItem, InventoryItem
import logging

logger = logging.getLogger(__name__)


class MIVItemSelectionDialog(QDialog):
    """دیالوگ انتخاب آیتم از انبار با قابلیت تطبیق هوشمند"""

    item_selected = pyqtSignal(dict)  # سیگنال برای ارسال آیتم انتخاب شده

    def __init__(self,
                 warehouse_service: WarehouseService,
                 item_matching_service: ItemMatchingService,
                 mto_item: Optional[MTOItem] = None,
                 parent=None):
        super().__init__(parent)
        self.warehouse_service = warehouse_service
        self.item_matching_service = item_matching_service
        self.mto_item = mto_item
        self.selected_item = None

        self.setWindowTitle("انتخاب آیتم از انبار")
        self.setModal(True)
        self.resize(1200, 700)

        self.setup_ui()
        self.load_initial_data()

    def setup_ui(self):
        """راه‌اندازی رابط کاربری"""
        layout = QVBoxLayout()

        # بخش اطلاعات MTO (اگر وجود دارد)
        if self.mto_item:
            mto_group = QGroupBox("اطلاعات درخواست از MTO")
            mto_layout = QHBoxLayout()

            mto_info = QLabel(
                f"کد: {self.mto_item.item_code} | "
                f"شرح: {self.mto_item.description} | "
                f"سایز: {self.mto_item.size_1 or '-'} | "
                f"مقدار درخواستی: {self.mto_item.qty}"
            )
            mto_info.setStyleSheet("font-weight: bold; color: #2196F3;")
            mto_layout.addWidget(mto_info)

            mto_group.setLayout(mto_layout)
            layout.addWidget(mto_group)

        # بخش جستجو
        search_group = QGroupBox("جستجو در انبار")
        search_layout = QVBoxLayout()

        # خط اول جستجو
        search_row1 = QHBoxLayout()

        search_row1.addWidget(QLabel("جستجو:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("کد، شرح یا مشخصات را وارد کنید...")
        self.search_input.textChanged.connect(self.on_search_changed)
        search_row1.addWidget(self.search_input, 2)

        search_row1.addWidget(QLabel("انبار:"))
        self.warehouse_combo = QComboBox()
        self.warehouse_combo.addItem("همه انبارها", None)
        self.warehouse_combo.currentIndexChanged.connect(self.filter_items)
        search_row1.addWidget(self.warehouse_combo)

        search_row1.addWidget(QLabel("وضعیت:"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(["همه", "موجود", "ناموجود"])
        self.status_combo.currentIndexChanged.connect(self.filter_items)
        search_row1.addWidget(self.status_combo)

        self.search_btn = QPushButton("🔍 جستجو")
        self.search_btn.clicked.connect(self.perform_search)
        search_row1.addWidget(self.search_btn)

        search_layout.addLayout(search_row1)

        # خط دوم - فیلترهای اضافی
        search_row2 = QHBoxLayout()

        search_row2.addWidget(QLabel("سایز:"))
        self.size_filter = QLineEdit()
        self.size_filter.setPlaceholderText("مثال: 2\"")
        self.size_filter.textChanged.connect(self.filter_items)
        search_row2.addWidget(self.size_filter)

        search_row2.addWidget(QLabel("مشخصات:"))
        self.spec_filter = QLineEdit()
        self.spec_filter.setPlaceholderText("مثال: SCH 40")
        self.spec_filter.textChanged.connect(self.filter_items)
        search_row2.addWidget(self.spec_filter)

        search_row2.addWidget(QLabel("Heat No:"))
        self.heat_filter = QLineEdit()
        self.heat_filter.textChanged.connect(self.filter_items)
        search_row2.addWidget(self.heat_filter)

        search_layout.addLayout(search_row2)

        search_group.setLayout(search_layout)
        layout.addWidget(search_group)

        # Splitter برای جداول
        splitter = QSplitter(Qt.Orientation.Vertical)

        # جدول نتایج تطبیق هوشمند
        smart_group = QGroupBox("پیشنهادهای هوشمند")
        smart_layout = QVBoxLayout()

        self.smart_table = QTableWidget()
        self.smart_table.setColumnCount(9)
        self.smart_table.setHorizontalHeaderLabels([
            "کد انبار", "شرح", "سایز", "مشخصات", "Heat No",
            "موجودی", "واحد", "امتیاز تطبیق", "نوع تطبیق"
        ])
        self.smart_table.horizontalHeader().setStretchLastSection(True)
        self.smart_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.smart_table.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.smart_table.itemSelectionChanged.connect(self.on_selection_changed)
        smart_layout.addWidget(self.smart_table)

        smart_group.setLayout(smart_layout)
        splitter.addWidget(smart_group)

        # جدول همه موجودی انبار
        all_group = QGroupBox("همه موارد موجود در انبار")
        all_layout = QVBoxLayout()

        self.inventory_table = QTableWidget()
        self.inventory_table.setColumnCount(8)
        self.inventory_table.setHorizontalHeaderLabels([
            "کد انبار", "شرح", "سایز", "مشخصات", "Heat No",
            "موجودی", "رزرو شده", "واحد"
        ])
        self.inventory_table.horizontalHeader().setStretchLastSection(True)
        self.inventory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.inventory_table.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.inventory_table.itemSelectionChanged.connect(self.on_selection_changed)
        all_layout.addWidget(self.inventory_table)

        all_group.setLayout(all_layout)
        splitter.addWidget(all_group)

        layout.addWidget(splitter)

        # بخش انتخاب مقدار و دکمه‌ها
        bottom_layout = QHBoxLayout()

        # اطلاعات آیتم انتخاب شده
        self.selected_info = QLabel("هیچ آیتمی انتخاب نشده")
        self.selected_info.setStyleSheet("padding: 5px; background-color: #f0f0f0;")
        bottom_layout.addWidget(self.selected_info, 2)

        bottom_layout.addWidget(QLabel("مقدار:"))
        self.quantity_spin = QDoubleSpinBox()
        self.quantity_spin.setMinimum(0.01)
        self.quantity_spin.setMaximum(99999)
        self.quantity_spin.setDecimals(2)
        if self.mto_item:
            self.quantity_spin.setValue(self.mto_item.qty)
        bottom_layout.addWidget(self.quantity_spin)

        self.select_btn = QPushButton("✓ انتخاب")
        self.select_btn.clicked.connect(self.accept_selection)
        self.select_btn.setEnabled(False)
        self.select_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
        bottom_layout.addWidget(self.select_btn)

        self.cancel_btn = QPushButton("✗ انصراف")
        self.cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(self.cancel_btn)

        layout.addLayout(bottom_layout)

        self.setLayout(layout)

        # Timer برای تاخیر در جستجو
        self.search_timer = QTimer()
        self.search_timer.timeout.connect(self.perform_search)
        self.search_timer.setSingleShot(True)

    def load_initial_data(self):
        """بارگذاری داده‌های اولیه"""
        try:
            # بارگذاری لیست انبارها
            warehouses = self.warehouse_service.get_all_warehouses()
            for warehouse in warehouses:
                self.warehouse_combo.addItem(warehouse.name, warehouse.id)

            # اگر MTO item داریم، پیشنهادهای هوشمند را بارگذاری کن
            if self.mto_item:
                self.load_smart_suggestions()

            # بارگذاری همه موجودی‌ها
            self.load_all_inventory()

        except Exception as e:
            logger.error(f"خطا در بارگذاری داده‌های اولیه: {e}")
            QMessageBox.critical(self, "خطا", f"خطا در بارگذاری داده‌ها: {str(e)}")

    def load_smart_suggestions(self):
        """بارگذاری پیشنهادهای هوشمند بر اساس MTO"""
        if not self.mto_item:
            return

        try:
            # دریافت پیشنهادهای هوشمند
            suggestions = self.item_matching_service.find_matches(
                source_code=self.mto_item.item_code,
                source_description=self.mto_item.description,
                source_size=self.mto_item.size_1,
                source_spec=self.mto_item.spec,
                limit=10
            )

            # نمایش در جدول
            self.smart_table.setRowCount(0)
            for suggestion in suggestions:
                row = self.smart_table.rowCount()
                self.smart_table.insertRow(row)

                # آیتم انبار
                inv_item = suggestion['inventory_item']

                # اضافه کردن داده‌ها به جدول
                self.smart_table.setItem(row, 0, QTableWidgetItem(inv_item.material_code))
                self.smart_table.setItem(row, 1, QTableWidgetItem(inv_item.description or ""))
                self.smart_table.setItem(row, 2, QTableWidgetItem(inv_item.size or ""))
                self.smart_table.setItem(row, 3, QTableWidgetItem(inv_item.specification or ""))
                self.smart_table.setItem(row, 4, QTableWidgetItem(inv_item.heat_no or ""))
                self.smart_table.setItem(row, 5, QTableWidgetItem(f"{inv_item.available_qty:.2f}"))
                self.smart_table.setItem(row, 6, QTableWidgetItem(inv_item.unit))

                # امتیاز و نوع تطبیق
                score_item = QTableWidgetItem(f"{suggestion['score']:.0%}")
                if suggestion['score'] >= 0.9:
                    score_item.setBackground(QColor(76, 175, 80))  # سبز
                elif suggestion['score'] >= 0.7:
                    score_item.setBackground(QColor(255, 193, 7))  # زرد
                else:
                    score_item.setBackground(QColor(255, 152, 0))  # نارنجی
                self.smart_table.setItem(row, 7, score_item)

                self.smart_table.setItem(row, 8, QTableWidgetItem(suggestion['match_type']))

                # ذخیره آیتم انبار در row
                self.smart_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, inv_item)

        except Exception as e:
            logger.error(f"خطا در بارگذاری پیشنهادهای هوشمند: {e}")


    def load_all_inventory(self):
        """بارگذاری همه آیتم‌های موجودی انبار"""
        try:
            items = self.warehouse_service.get_all_inventory_items()
            self.inventory_table.setRowCount(0)
            for inv_item in items:
                row = self.inventory_table.rowCount()
                self.inventory_table.insertRow(row)
                self.inventory_table.setItem(row, 0, QTableWidgetItem(inv_item.material_code))
                self.inventory_table.setItem(row, 1, QTableWidgetItem(inv_item.description or ""))
                self.inventory_table.setItem(row, 2, QTableWidgetItem(inv_item.size or ""))
                self.inventory_table.setItem(row, 3, QTableWidgetItem(inv_item.specification or ""))
                self.inventory_table.setItem(row, 4, QTableWidgetItem(inv_item.heat_no or ""))
                self.inventory_table.setItem(row, 5, QTableWidgetItem(f"{inv_item.physical_qty:.2f}"))
                self.inventory_table.setItem(row, 6, QTableWidgetItem(f"{inv_item.reserved_qty:.2f}"))
                self.inventory_table.setItem(row, 7, QTableWidgetItem(inv_item.unit))
                self.inventory_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, inv_item)
        except Exception as e:
            logger.error(f"خطا در بارگذاری موجودی‌ها: {e}")

    def filter_items(self):
        """فیلتر کردن آیتم‌های جدول بر اساس فیلترها"""
        search_text = self.search_input.text().lower()
        size_text = self.size_filter.text().lower()
        spec_text = self.spec_filter.text().lower()
        heat_text = self.heat_filter.text().lower()

        # فیلتر برای جدول هوشمند
        for row in range(self.smart_table.rowCount()):
            show_row = True

            if size_text and size_text not in (
            self.smart_table.item(row, 2).text().lower() if self.smart_table.item(row, 2) else ""):
                show_row = False
            if spec_text and spec_text not in (
            self.smart_table.item(row, 3).text().lower() if self.smart_table.item(row, 3) else ""):
                show_row = False
            if heat_text and heat_text not in (
            self.smart_table.item(row, 4).text().lower() if self.smart_table.item(row, 4) else ""):
                show_row = False

            self.smart_table.setRowHidden(row, not show_row)

        # فیلتر برای جدول کل موجودی
        for row in range(self.inventory_table.rowCount()):
            show_row = True

            if size_text and size_text not in (
            self.inventory_table.item(row, 2).text().lower() if self.inventory_table.item(row, 2) else ""):
                show_row = False
            if spec_text and spec_text not in (
            self.inventory_table.item(row, 3).text().lower() if self.inventory_table.item(row, 3) else ""):
                show_row = False
            if heat_text and heat_text not in (
            self.inventory_table.item(row, 4).text().lower() if self.inventory_table.item(row, 4) else ""):
                show_row = False

            self.inventory_table.setRowHidden(row, not show_row)

    def perform_search(self):
        """اجرای جستجو بر اساس ورودی کاربر"""
        term = self.search_input.text().strip()
        wh_id = self.warehouse_combo.currentData()
        status = self.status_combo.currentText()

        results = self.warehouse_service.search_inventory(
            search_term=term,
            warehouse_id=wh_id,
            status_filter=status,
            size=self.size_filter.text().strip(),
            spec=self.spec_filter.text().strip(),
            heat_no=self.heat_filter.text().strip()
        )
        # نمایش نتایج مشابه load_all_inventory

    def on_item_double_clicked(self, item):
        self.accept_selection()

    def on_selection_changed(self):
        selected = self.smart_table.selectedItems() or self.inventory_table.selectedItems()
        if selected:
            inv_item = selected[0].data(Qt.ItemDataRole.UserRole)
            self.selected_item = inv_item
            self.selected_info.setText(f"{inv_item.material_code} | {inv_item.description}")
            self.select_btn.setEnabled(True)

    def accept_selection(self):
        if not self.selected_item:
            return
        qty = self.quantity_spin.value()
        self.item_selected.emit({
            'inventory_item': self.selected_item,
            'quantity': qty
        })
        self.accept()

    def on_search_changed(self, text):
        """تاخیر در جستجو برای جلوگیری از جستجوی مکرر"""
        self.search_timer.stop()
        if len(text) >= 2:  # حداقل 2 کاراکتر
            self.search_timer.start(500)  # 500ms تاخیر

    def accept_selection(self):
        if not self.selected_item:
            return
        qty = self.quantity_spin.value()

        # ذخیره برای دسترسی بعدی
        self.selected_data = {
            'inventory_item': self.selected_item,
            'quantity': qty
        }

        self.item_selected.emit(self.selected_data)
        self.accept()

