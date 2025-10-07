from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from data_manager_facade import DataManagerFacade as DataManager
from functools import partial
from models import *
import os
import sys


class MTOConsumptionDialog(QDialog):
    def __init__(self, dm: DataManager, project_id: int, line_no: str, miv_record_id: int = None, parent=None):
        super().__init__(parent)
        self.dm = dm
        self.project_id = project_id
        self.line_no = line_no
        self.miv_record_id = miv_record_id

        # Data storage
        self.consumed_data = []  # For direct MTO consumption
        self.spool_consumption_data = []  # For spool consumption
        self.spool_selections = {}  # Internal UI mapping: {row_index: [list of spool selections]}

        self.existing_consumptions = {}
        # We don't need to fetch existing spool consumptions as the logic
        # is handled by the data manager during the update.

        self.setWindowTitle(f"مدیریت مصرف برای خط: {self.line_no}")
        self.setMinimumSize(1200, 600)

        if self.miv_record_id:
            self.setWindowTitle(f"ویرایش آیتم‌های MIV ID: {self.miv_record_id}")
            self.existing_consumptions = self.dm.get_consumptions_for_miv(self.miv_record_id)

        layout = QVBoxLayout(self)
        info_label = QLabel(
            "مقدار مصرف مستقیم را وارد کنید یا از دکمه 'انتخاب اسپول' برای برداشت از انبار اسپول استفاده نمایید.")
        layout.addWidget(info_label)

        self.table = QTableWidget()
        self.table.setColumnCount(15)
        self.table.setHorizontalHeaderLabels([
            # MTO Info
            "Item Code", "Description", "Total Qty", "Used (All)", "Remaining", "Unit",
            # New MTO Details
            "Bore", "Type",
            # Consumption for this MIV
            "مصرف مستقیم",
            # Spool Info
            "انتخاب اسپول", "Spool ID", "Qty from Spool", "Spool Remaining",
            # 🆕 Warehouse Selection (فاز 2)
            "انتخاب از انبار", "مقدار از انبار"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        self.populate_table()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept_data)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def populate_table(self):
        self.progress_data = self.dm.get_enriched_line_progress(self.project_id, self.line_no, readonly=False)
        self.table.setRowCount(len(self.progress_data))

        for row_idx, item in enumerate(self.progress_data):
            mto_item_id = item["mto_item_id"]

            # ستون‌های MTO (0-7)
            self.table.setItem(row_idx, 0, QTableWidgetItem(item["Item Code"] or ""))
            self.table.setItem(row_idx, 1, QTableWidgetItem(item["Description"] or ""))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(item["Total Qty"])))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(item["Used Qty"])))
            remaining_qty = item["Remaining Qty"] or 0
            self.table.setItem(row_idx, 4, QTableWidgetItem(str(remaining_qty)))
            self.table.setItem(row_idx, 5, QTableWidgetItem(item["Unit"] or ""))
            self.table.setItem(row_idx, 6, QTableWidgetItem(str(item.get("Bore") or "")))
            self.table.setItem(row_idx, 7, QTableWidgetItem(item.get("Type") or ""))

            # مصرف موجود در این MIV
            current_miv_total_usage = self.existing_consumptions.get(mto_item_id, 0)

            # SpinBox برای مصرف مستقیم
            spin_box = QDoubleSpinBox()
            max_val = remaining_qty + current_miv_total_usage
            spin_box.setRange(0, max_val)
            spin_box.setDecimals(2)
            spin_box.setValue(current_miv_total_usage)
            self.table.setCellWidget(row_idx, 8, spin_box)

            # دکمه انتخاب اسپول
            spool_btn = QPushButton("انتخاب...")

            # --- NEW: بررسی سازگاری آیتم با انبار اسپول ---
            item_type = item.get("Type")
            p1_bore = item.get("Bore")
            # از تابع جدید DataManager برای گرفتن آیتم‌های سازگار استفاده می‌کنیم
            matching_items = self.dm.get_mapped_spool_items(item_type, p1_bore)

            if not matching_items:  # 🚫 اگر هیچ اسپولی پیدا نشد
                spool_btn.setEnabled(False)
                spool_btn.setToolTip("هیچ آیتم سازگاری در انبار اسپول یافت نشد.")

            spool_btn.clicked.connect(partial(self.handle_spool_selection, row_idx))
            self.table.setCellWidget(row_idx, 9, spool_btn)

            # ستون‌های اطلاعات اسپول
            for col in [10, 11, 12]:
                self.table.setItem(row_idx, col, QTableWidgetItem(""))

            # اگر کلا آیتمی باقی نمانده، همه کنترل‌ها غیرفعال شوند
            if max_val <= 0:
                spin_box.setEnabled(False)
                spool_btn.setEnabled(False)

            # ستون‌های اطلاعاتی فقط-خواندنی
            for col in list(range(8)) + [10, 11, 12]:
                item_widget = self.table.item(row_idx, col)
                if item_widget:
                    item_widget.setFlags(item_widget.flags() & ~Qt.ItemFlag.ItemIsEditable)


            warehouse_btn = QPushButton("📦 انبار")
            warehouse_btn.setToolTip("انتخاب آیتم از انبار عمومی")
            warehouse_btn.clicked.connect(partial(self.handle_warehouse_selection, row_idx))
            self.table.setCellWidget(row_idx, 13, warehouse_btn)

            # 🆕 نمایش مقدار انتخاب شده از انبار (ستون 14)
            self.table.setItem(row_idx, 14, QTableWidgetItem("0"))

        self.table.resizeColumnsToContents()

    def handle_warehouse_selection(self, row_idx):
        """باز کردن دیالوگ انتخاب از انبار عمومی"""
        from ui.dialogs.miv_item_selection_dialog import MIVItemSelectionDialog

        item_data = self.progress_data[row_idx]

        # ایجاد یک آبجکت موقت MTOItem برای ارسال به دیالوگ
        class TempMTOItem:
            def __init__(self, data):
                self.id = data["mto_item_id"]
                self.item_code = data["Item Code"]
                self.description = data["Description"]
                self.size_1 = data.get("Bore", "")
                self.spec = data.get("Type", "")
                self.qty = data["Remaining Qty"] or 0

        temp_mto = TempMTOItem(item_data)

        # باز کردن دیالوگ انتخاب از انبار
        dialog = MIVItemSelectionDialog(
            warehouse_service=self.dm.warehouse_service,
            item_matching_service=self.dm.item_matching_service,
            mto_item=temp_mto,
            parent=self
        )

        if dialog.exec():
            selected_data = dialog.selected_data
            if selected_data:
                # ذخیره انتخاب
                if not hasattr(self, 'warehouse_selections'):
                    self.warehouse_selections = {}

                self.warehouse_selections[row_idx] = {
                    'inventory_item_id': selected_data['inventory_item'].id,
                    'quantity': selected_data['quantity'],
                    'item_code': selected_data['inventory_item'].material_code,
                    'description': selected_data['inventory_item'].description
                }

                # به‌روزرسانی جدول
                self.table.item(row_idx, 14).setText(str(selected_data['quantity']))

                # تنظیم مقدار مصرف مستقیم
                remaining = item_data["Remaining Qty"] or 0
                current_miv = self.existing_consumptions.get(item_data["mto_item_id"], 0)
                spool_qty = float(self.table.item(row_idx, 11).text() or 0)
                warehouse_qty = selected_data['quantity']

                spin_box = self.table.cellWidget(row_idx, 8)
                new_max = (remaining + current_miv) - spool_qty - warehouse_qty
                spin_box.setRange(0, max(0, new_max))

    def handle_spool_selection(self, row_idx):
        item_data = self.progress_data[row_idx]
        item_type = item_data.get("Type")
        p1_bore = item_data.get("Bore")

        # --- NEW: Get the remaining quantity for the MTO item ---
        remaining_qty = item_data.get("Remaining Qty", 0)

        if not item_type:
            self.parent().show_message("هشدار", "نوع آیتم (Type) برای این ردیف MTO مشخص نشده است.", "warning")
            return

        # 🔹 استفاده درست از item_data به جای item
        matching_items = self.dm.get_mapped_spool_items(item_type, p1_bore)

        if not matching_items:
            self.parent().show_message(
                "اطلاعات",
                f"هیچ اسپول سازگار برای نوع '{item_type}' و سایز '{p1_bore}' یافت نشد.",
                "info"
            )
            return

        # --- CHANGE: Pass the remaining_qty to the dialog ---
        dialog = SpoolSelectionDialog(matching_items, remaining_qty, self)
        if dialog.exec():
            selected_spools = dialog.get_selected_data()
            self.spool_selections[row_idx] = selected_spools
            self.update_row_after_spool_selection(row_idx)

    def update_row_after_spool_selection(self, row_idx):
        selections = self.spool_selections.get(row_idx, [])
        if not selections:
            self.table.item(row_idx, 10).setText("")
            self.table.item(row_idx, 11).setText("")
            self.table.item(row_idx, 12).setText("")
            return

        total_spool_qty = sum(s['used_qty'] for s in selections)

        session = self.dm.get_session()
        try:
            first_selection = selections[0]
            spool_item = session.get(SpoolItem, first_selection['spool_item_id'])
            spool_id_text = str(spool_item.spool.spool_id)
            if len(selections) > 1:
                spool_id_text += f" (+{len(selections) - 1} more)"

            self.table.item(row_idx, 10).setText(spool_id_text)  # Spool ID
            self.table.item(row_idx, 11).setText(str(total_spool_qty))  # Qty from Spool
            self.table.item(row_idx, 12).setText(str(spool_item.qty_available - first_selection['used_qty']))
        finally:
            session.close()

        item_data = self.progress_data[row_idx]
        remaining_qty = item_data["Remaining Qty"] or 0
        current_miv_usage = self.existing_consumptions.get(item_data["mto_item_id"], 0)

        spin_box = self.table.cellWidget(row_idx, 8)
        new_max = (remaining_qty + current_miv_usage) - total_spool_qty
        spin_box.setRange(0, max(0, new_max))
        if spin_box.value() > new_max:
            spin_box.setValue(max(0, new_max))

    def accept_data(self):
        self.consumed_data = []
        self.spool_consumption_data = []
        self.warehouse_consumption_data = []

        for row in range(self.table.rowCount()):
            mto_item_id = self.progress_data[row]["mto_item_id"]

            # مصرف مستقیم
            spin_box = self.table.cellWidget(row, 8)
            direct_qty = spin_box.value() if spin_box else 0
            if direct_qty > 0.001:
                self.consumed_data.append({
                    "mto_item_id": mto_item_id,
                    # --- CHANGE: گرد کردن مقدار نهایی ---
                    "used_qty": round(direct_qty, 2)
                })

            # مصرف اسپول (مقادیر از دیالوگ دیگر گرد شده می‌آیند)
            if row in self.spool_selections:
                for sel in self.spool_selections[row]:
                    self.spool_consumption_data.append({
                        "spool_item_id": sel["spool_item_id"],
                        "used_qty": sel["used_qty"]  # این مقدار از قبل گرد شده
                    })

            # 🆕 مصرف از انبار عمومی
            if hasattr(self, 'warehouse_selections') and row in self.warehouse_selections:
                warehouse_data = self.warehouse_selections[row]
                self.warehouse_consumption_data.append({
                    "mto_item_id": mto_item_id,
                    "inventory_item_id": warehouse_data['inventory_item_id'],
                    "used_qty": warehouse_data['quantity']
                })

        self.accept()

    def get_data(self):
        return self.consumed_data, self.spool_consumption_data, getattr(self, 'warehouse_consumption_data', [])


class SpoolSelectionDialog(QDialog):
    def __init__(self, matching_items: list[SpoolItem], remaining_mto_qty: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتخاب آیتم از انبار اسپول")
        self.setMinimumSize(1200, 700)

        self.selected_data = []
        self.items = matching_items
        self.remaining_mto_qty = remaining_mto_qty

        layout = QVBoxLayout(self)

        # بخش فیلتر
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

        # جدول
        self.table = QTableWidget()
        self.table.setColumnCount(14)
        self.table.setHorizontalHeaderLabels([
            "ID", "Spool ID", "Item Code", "Comp. Type", "Class/Angle", "Bore1", "Bore2",
            "Material", "Schedule", "Thickness", "Length", "Qty Avail.", "موجودی", "مقدار مصرف"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        self.populate_table()

        # دکمه‌ها
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept_data)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def populate_table(self):
        self.table.setRowCount(len(self.items))
        self.spin_boxes_info = {}

        for row, item in enumerate(self.items):
            # ستون‌های اطلاعاتی
            self.table.setItem(row, 0, QTableWidgetItem(str(item.id)))
            self.table.setItem(row, 1, QTableWidgetItem(item.spool.spool_id))
            self.table.setItem(row, 2, QTableWidgetItem(item.item_code or ""))
            self.table.setItem(row, 3, QTableWidgetItem(item.component_type or ""))
            self.table.setItem(row, 4, QTableWidgetItem(item.class_angle or ""))
            self.table.setItem(row, 5, QTableWidgetItem(str(item.p1_bore) if item.p1_bore else ""))
            self.table.setItem(row, 6, QTableWidgetItem(str(item.p2_bore) if item.p2_bore else ""))
            self.table.setItem(row, 7, QTableWidgetItem(item.material or ""))
            self.table.setItem(row, 8, QTableWidgetItem(item.schedule or ""))
            self.table.setItem(row, 9, QTableWidgetItem(str(item.thickness) if item.thickness else ""))
            self.table.setItem(row, 10, QTableWidgetItem(str(item.length) if item.length else ""))
            self.table.setItem(row, 11, QTableWidgetItem(str(item.qty_available)))

            # ستون موجودی
            remaining_label = QLabel(f"{item.qty_available:.2f}")
            remaining_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, 12, remaining_label)

            # SpinBox برای مقدار مصرف
            spin_box = QDoubleSpinBox()
            spin_box.setRange(0, min(item.qty_available, self.remaining_mto_qty))
            spin_box.setDecimals(2)
            spin_box.setSingleStep(0.1)
            spin_box.setValue(0)

            # <<< NEW: اتصال تغییرات برای به‌روزرسانی جمع کل
            spin_box.valueChanged.connect(self.update_totals)

            self.table.setCellWidget(row, 13, spin_box)
            self.spin_boxes_info[row] = {
                'spin_box': spin_box,
                'remaining_label': remaining_label,
                'item': item
            }

        self.table.resizeColumnsToContents()

    def accept_data(self):
        self.selected_data = []

        for row, info in self.spin_boxes_info.items():
            spin_box = info['spin_box']
            used_qty = spin_box.value()

            if used_qty > 0:
                self.selected_data.append({
                    'spool_item_id': info['item'].id,
                    'used_qty': round(used_qty, 2)  # --- CHANGE: گرد کردن مقدار
                })

        self.accept()

    def get_selected_data(self):
        return self.selected_data

    def filter_table(self):
        """فیلتر کردن جدول بر اساس ورودی‌های فیلتر"""
        for row in range(self.table.rowCount()):
            should_hide = False

            for col_idx, filter_input in self.filters.items():
                filter_text = filter_input.text().lower()
                if filter_text:
                    item = self.table.item(row, col_idx)
                    if item and filter_text not in item.text().lower():
                        should_hide = True
                        break

            self.table.setRowHidden(row, should_hide)

    def update_totals(self):
        """<<< NEW: محاسبه و نمایش جمع کل انتخاب شده"""
        total = 0
        for info in self.spin_boxes_info.values():
            total += info['spin_box'].value()

        self.total_selected_label.setText(f"جمع انتخاب شده: {total:.2f}")

        # بررسی عدم تجاوز از مقدار MTO
        if total > self.remaining_mto_qty:
            self.total_selected_label.setStyleSheet(
                "font-weight: bold; padding: 5px; background-color: #ffcccc; color: red;"
            )
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        else:
            self.total_selected_label.setStyleSheet(
                "font-weight: bold; padding: 5px; background-color: #d1e7dd;"
            )
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
