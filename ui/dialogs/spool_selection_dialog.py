from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from models import *
import os
import sys

class SpoolSelectionDialog(QDialog):
    def __init__(self, matching_items: list[SpoolItem], remaining_mto_qty: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتخاب آیتم از انبار اسپول")
        self.setMinimumSize(1200, 700)

        self.selected_data = []
        self.items = matching_items
        self.remaining_mto_qty = remaining_mto_qty

        layout = QVBoxLayout(self)

        # ... (بخش فیلتر بدون تغییر باقی می‌ماند) ...
        filter_group = QGroupBox("فیلتر")
        filter_layout = QGridLayout(filter_group)
        self.filters = {}
        filter_definitions = {"Item Code": 2, "Comp. Type": 3, "Material": 7, "Bore1": 5}
        col = 0
        for label, col_idx in filter_definitions.items():
            filter_label = QLabel(f"{label}:")
            filter_input = QLineEdit()
            filter_input.setPlaceholderText(f"جستجو بر اساس {label}...")
            filter_input.textChanged.connect(self.filter_table)
            filter_layout.addWidget(filter_label, 0, col)
            filter_layout.addWidget(filter_input, 0, col + 1)
            self.filters[col_idx] = filter_input
            col += 2
        layout.addWidget(filter_group)

        # --- بخش اطلاعات با لیبل جدید ---
        info_layout = QHBoxLayout()
        info_label = QLabel(f"مقدار کل باقی‌مانده از MTO: {self.remaining_mto_qty}")
        info_label.setStyleSheet("background-color: #f1fa8c; padding: 5px; border-radius: 3px;")

        # <<< NEW: لیبل برای نمایش جمع کل انتخاب شده
        self.total_selected_label = QLabel("جمع انتخاب شده: 0.0")
        self.total_selected_label.setStyleSheet("font-weight: bold; padding: 5px; background-color: #d1e7dd;")

        info_layout.addWidget(info_label, 1)
        info_layout.addWidget(self.total_selected_label)
        layout.addLayout(info_layout)

        # ... (بخش جدول و دکمه‌ها بدون تغییر باقی می‌ماند) ...
        self.table = QTableWidget()
        self.table.setColumnCount(14)
        self.table.setHorizontalHeaderLabels([
            "ID", "Spool ID", "Item Code", "Comp. Type", "Class/Angle", "Bore1", "Bore2",
            "Material", "Schedule", "Thickness", "Length", "Qty Avail.", "موجودی", "مقدار مصرف"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        self.populate_table()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept_data)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def populate_table(self):
        self.spin_boxes_info = []
        self.table.setRowCount(0)  # <<< ADDED: پاک کردن جدول قبل از پر کردن
        self.table.setRowCount(len(self.items))

        for row, item in enumerate(self.items):
            self.table.setItem(row, 0, QTableWidgetItem(str(item.id)))
            self.table.setItem(row, 1, QTableWidgetItem(str(item.spool.spool_id)))
            # ... (ستون‌های 2 تا 9 بدون تغییر)
            self.table.setItem(row, 2, QTableWidgetItem(item.item_code or ""))
            self.table.setItem(row, 3, QTableWidgetItem(item.component_type or ""))
            self.table.setItem(row, 4, QTableWidgetItem(str(item.class_angle) if item.class_angle is not None else ""))
            self.table.setItem(row, 5, QTableWidgetItem(str(item.p1_bore or "")))
            self.table.setItem(row, 6, QTableWidgetItem(str(item.p2_bore or "")))
            self.table.setItem(row, 7, QTableWidgetItem(item.material or ""))
            self.table.setItem(row, 8, QTableWidgetItem(item.schedule or ""))
            self.table.setItem(row, 9, QTableWidgetItem(str(item.thickness or "")))

            self.table.setItem(row, 10, QTableWidgetItem(str(item.length or "")))
            self.table.setItem(row, 11, QTableWidgetItem(str(item.qty_available or "")))

            # --- CHANGE: حذف تبدیل واحد ---
            is_pipe = "PIPE" in (item.component_type or "").upper()
            if is_pipe:
                available_qty_for_ui = item.length or 0  # دیگر تقسیم بر ۱۰۰۰ نداریم
            else:
                available_qty_for_ui = item.qty_available or 0

            # نمایش موجودی با دو رقم اعشار
            self.table.setItem(row, 12, QTableWidgetItem(f"{available_qty_for_ui:.2f}"))

            spin_box = QDoubleSpinBox()
            spin_box.setRange(0, available_qty_for_ui)
            # --- CHANGE: تنظیم دقت به ۲ رقم اعشار ---
            spin_box.setDecimals(2)
            spin_box.valueChanged.connect(self.update_totals)
            self.table.setCellWidget(row, 13, spin_box)

            self.spin_boxes_info.append({'widget': spin_box, 'max_avail': available_qty_for_ui})

            for col in range(13):
                cell_item = self.table.item(row, col)
                if cell_item:
                    cell_item.setFlags(cell_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        self.update_totals()

    def accept_data(self):
        self.selected_data = []
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue

            spin_box = self.table.cellWidget(row, 13)
            used_qty_from_ui = spin_box.value()

            if used_qty_from_ui > 0.001:
                spool_item_id = int(self.table.item(row, 0).text())

                # --- CHANGE: حذف تبدیل واحد و گرد کردن نهایی ---
                used_qty_for_db = round(used_qty_from_ui, 2)

                self.selected_data.append({
                    "spool_item_id": spool_item_id,
                    "used_qty": used_qty_for_db
                })
        self.accept()

    def get_selected_data(self):
        return self.selected_data

    def filter_table(self):
        """Hides rows that do not match the filter criteria."""
        # --- CHANGE: تبدیل به حروف بزرگ برای جستجوی غیرحساس به بزرگی و کوچکی ---
        filter_texts = {col: f.text().upper() for col, f in self.filters.items()}

        for row in range(self.table.rowCount()):
            is_visible = True
            for col, filter_text in filter_texts.items():
                if not filter_text:
                    continue
                item = self.table.item(row, col)
                # --- CHANGE: متن سلول هم به حروف بزرگ تبدیل می‌شود ---
                if not item or filter_text not in item.text().upper():
                    is_visible = False
                    break
            self.table.setRowHidden(row, not is_visible)

    def update_totals(self):
        """Calculates the total selected quantity and dynamically updates the limits of all spin boxes."""
        current_total = sum(info['widget'].value() for info in self.spin_boxes_info)

        # --- CHANGE: آپدیت لیبل با دو رقم اعشار ---
        self.total_selected_label.setText(f"جمع انتخاب شده: {current_total:.2f}")
        if current_total > self.remaining_mto_qty:
            self.total_selected_label.setStyleSheet("font-weight: bold; padding: 5px; background-color: #f8d7da;")
        else:
            self.total_selected_label.setStyleSheet("font-weight: bold; padding: 5px; background-color: #d1e7dd;")

        remaining_headroom = self.remaining_mto_qty - current_total

        for info in self.spin_boxes_info:
            spin_box = info['widget']
            new_max = min(info['max_avail'], spin_box.value() + remaining_headroom)

            spin_box.blockSignals(True)
            spin_box.setMaximum(max(0, new_max))
            spin_box.blockSignals(False)
