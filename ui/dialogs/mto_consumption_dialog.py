from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from data_manager_facade import DataManagerFacade as DataManager
from functools import partial
from models import *
import os
import sys
from PyQt6.QtGui import QColor


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
            "انتخاب از انبار", "انبار (کد-مقدار)"  # تغییر نام ستون 14
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        self.populate_table()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept_data)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _check_warehouse_availability(self, mto_item_id):
        """
        بررسی موجودی انبار برای یک آیتم MTO

        Args:
            mto_item_id: شناسه آیتم MTO

        Returns:
            dict یا None: اطلاعات موجودی
        """
        try:
            result = self.dm.find_and_reserve_for_mto(
                mto_item_id=mto_item_id,
                auto_reserve=False
            )

            if result['success'] and result.get('items'):
                total_available = sum(item.get('available_qty', 0) for item in result['items'])
                return {
                    'available': total_available > 0,
                    'available_qty': total_available,
                    'items': result['items']
                }
        except Exception as e:
            print(f"Error checking warehouse: {e}")

        return None

    def populate_table(self):
        """پر کردن جدول اطلاعات MTO با کنترل‌های تعاملی"""
        self.progress_data = self.dm.get_enriched_line_progress(self.project_id, self.line_no, readonly=False)
        self.table.setRowCount(len(self.progress_data))

        for row_idx, item in enumerate(self.progress_data):
            mto_item_id = item["mto_item_id"]
            remaining_qty = item.get("Remaining Qty", 0)
            self.table.setItem(row_idx, 0, QTableWidgetItem(item.get("Item Code", "")))
            self.table.setItem(row_idx, 1, QTableWidgetItem(item.get("Description", "")))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(item.get("Total Qty", 0))))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(item.get("Used Qty", 0))))
            self.table.setItem(row_idx, 4, QTableWidgetItem(str(remaining_qty)))
            self.table.setItem(row_idx, 5, QTableWidgetItem(item.get("Unit", "")))
            self.table.setItem(row_idx, 6, QTableWidgetItem(str(item.get("Bore", ""))))
            self.table.setItem(row_idx, 7, QTableWidgetItem(item.get("Type", "")))

            # رنگ‌بندی برای موجودی
            if remaining_qty <= 0:
                self.table.item(row_idx, 4).setBackground(QColor(255, 200, 200))
            elif remaining_qty < 0.2 * item.get("Total Qty", 1):
                self.table.item(row_idx, 4).setBackground(QColor(255, 255, 200))
            else:
                self.table.item(row_idx, 4).setBackground(QColor(200, 255, 200))

            # SpinBox مصرف مستقیم
            spin_box = QDoubleSpinBox()
            current_miv_usage = self.existing_consumptions.get(mto_item_id, 0)
            max_val = remaining_qty + current_miv_usage
            spin_box.setRange(0, max_val)
            spin_box.setDecimals(2)
            spin_box.setValue(current_miv_usage)
            self.table.setCellWidget(row_idx, 8, spin_box)

            # انتخاب اسپول
            spool_btn = QPushButton("انتخاب...")
            item_type = item.get("Type")
            p1_bore = item.get("Bore")
            matching = self.dm.get_mapped_spool_items(item_type, p1_bore)
            if not matching:
                spool_btn.setEnabled(False)
                spool_btn.setToolTip("هیچ اسپول سازگار یافت نشد.")
            spool_btn.clicked.connect(partial(self.handle_spool_selection, row_idx))
            self.table.setCellWidget(row_idx, 9, spool_btn)

            # ستون‌های اسپول (10-12)
            for col in [10, 11, 12]:
                self.table.setItem(row_idx, col, QTableWidgetItem(""))

            # دکمه انبار
            btn = QPushButton("📦 انبار")
            btn.setToolTip("انتخاب آیتم از انبار عمومی")
            btn.clicked.connect(partial(self.handle_warehouse_selection, row_idx))
            self.table.setCellWidget(row_idx, 13, btn)

            # ستون نمایش مقدار انبار
            self.table.setItem(row_idx, 14, QTableWidgetItem("0"))

            if max_val <= 0:
                spin_box.setEnabled(False)
                spool_btn.setEnabled(False)
                btn.setEnabled(False)

        self.table.resizeColumnsToContents()

    def handle_warehouse_selection(self, row_idx):
        """باز کردن دیالوگ انتخاب از انبار عمومی با قابلیت یادگیری خودکار"""
        try:
            from ui.dialogs.miv_item_selection_dialog import MIVItemSelectionDialog
            from PyQt6.QtWidgets import QMessageBox
            from PyQt6.QtGui import QColor

            item_data = self.progress_data[row_idx]

            # ایجاد یک آبجکت موقت MTOItem برای ارسال به دیالوگ
            class TempMTOItem:
                def __init__(self, data):
                    self.id = data["mto_item_id"]
                    self.item_code = data["Item Code"]
                    self.description = data["Description"]
                    self.size = data.get("Bore", "")
                    self.size_1 = data.get("Bore", "")  # برای سازگاری
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

            # تزریق اطلاعات
            dialog.current_user = getattr(self.parent(), 'current_user', 'Unknown')
            dialog.current_project_id = self.project_id

            if dialog.exec():
                selected_data = dialog.get_selected_data()
                if selected_data:
                    # ذخیره انتخاب
                    if not hasattr(self, 'warehouse_selections'):
                        self.warehouse_selections = {}

                    self.warehouse_selections[row_idx] = {
                        'inventory_item_id': selected_data.get('inventory_item_id'),
                        'quantity': selected_data.get('used_qty', 0),
                        'item_code': selected_data.get('item_code', ''),
                        'description': selected_data.get('description', ''),
                        'warehouse_code': selected_data.get('warehouse_code', ''),
                        'warehouse_name': selected_data.get('warehouse_name', ''),
                        'size': selected_data.get('size', ''),
                        'type': selected_data.get('type', ''),
                        'unit': selected_data.get('unit', 'EA'),
                        'match_type': selected_data.get('match_type', 'MANUAL'),
                        'confidence_score': selected_data.get('confidence_score', 1.0)
                    }

                    # نمایش بهتر در جدول
                    quantity = selected_data.get('used_qty', 0)
                    warehouse_code = selected_data.get('warehouse_code', '')
                    item_code = selected_data.get('item_code', '')

                    # نمایش ترکیبی: مقدار + کد آیتم انبار
                    display_text = f"{quantity:.2f} از {item_code}"
                    if len(display_text) > 30:
                        display_text = f"{quantity:.2f} ({warehouse_code})"

                    self.table.item(row_idx, 14).setText(display_text)

                    # اضافه کردن tooltip کامل
                    tooltip_text = (
                        f"📦 انتخاب از انبار:\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"کد آیتم انبار: {item_code}\n"
                        f"شرح: {selected_data.get('description', '')[:50]}...\n"
                        f"انبار: {selected_data.get('warehouse_name', '')} ({warehouse_code})\n"
                        f"مقدار انتخابی: {quantity:.2f} {selected_data.get('unit', 'EA')}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"نوع تطابق: {selected_data.get('match_type', 'MANUAL')}\n"
                        f"درصد اطمینان: {selected_data.get('confidence_score', 1.0):.0%}"
                    )
                    self.table.item(row_idx, 14).setToolTip(tooltip_text)

                    # رنگ‌آمیزی بر اساس نوع تطابق
                    match_type = selected_data.get('match_type', 'MANUAL')
                    color = QColor(240, 240, 240)
                    if match_type == 'EXACT':
                        color = QColor(200, 255, 200)  # سبز روشن
                    elif match_type == 'RULE':
                        color = QColor(255, 255, 200)  # زرد روشن
                    elif match_type == 'NLP':
                        color = QColor(200, 230, 255)  # آبی روشن

                    self.table.item(row_idx, 14).setBackground(color)

                    # بروزرسانی محدودیت SpinBox
                    remaining = item_data["Remaining Qty"] or 0
                    current_miv = self.existing_consumptions.get(item_data["mto_item_id"], 0)
                    spool_qty = float(self.table.item(row_idx, 11).text() or 0)

                    spin_box = self.table.cellWidget(row_idx, 8)
                    if spin_box:
                        max_direct = max(0, (remaining + current_miv) - spool_qty - quantity)
                        spin_box.setRange(0, max_direct)

                    # نمایش پیام موفقیت
                    message = (
                        f"✅ آیتم انتخاب شد:\n"
                        f"کد انبار: {item_code}\n"
                        f"مقدار: {quantity:.2f} {selected_data.get('unit', 'EA')}"
                    )

                    try:
                        # تلاش برای استفاده از show_message والد
                        if hasattr(self.parent(), 'show_message'):
                            self.parent().show_message("انتخاب موفق", message, "info")
                    except:
                        # در صورت عدم موفقیت، استفاده از MessageBox
                        QMessageBox.information(self, "انتخاب موفق", message)

        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "خطا", f"خطا در انتخاب از انبار:\n{str(e)}")

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
        """
        جمع‌آوری مقادیر مصرف شده جهت ثبت MIV
        Returns:
            tuple: (consumed_items, spool_items)
            consumed_items: لیست دیکشنری‌ها با فیلدهای استاندارد MIV
            spool_items: لیست آیتم‌های اسپول
        """
        consumed_items = []
        spool_items = []

        for row_idx, item in enumerate(self.progress_data):
            mto_item_id = item["mto_item_id"]

            # مصرف مستقیم از SpinBox
            spin_box = self.table.cellWidget(row_idx, 8)
            direct_qty = spin_box.value() if spin_box else 0

            # مصرف از انبار
            warehouse_selection = getattr(self, 'warehouse_selections', {}).get(row_idx, {})
            warehouse_qty = warehouse_selection.get('quantity', 0)

            # مجموع کل مصرف
            total_consumed = direct_qty + warehouse_qty

            if total_consumed > 0:
                # ساخت دیکشنری با فرمت مورد انتظار main_window
                consumed_item = {
                    'mto_item_id': mto_item_id,
                    'used_qty': total_consumed,  # فیلد کلیدی که main_window انتظار داره
                    'item_code': item.get("Item Code", ""),
                    'description': item.get("Description", ""),
                    'unit': item.get("Unit", "EA"),
                    'bore': item.get("Bore", ""),
                    'type': item.get("Type", "")
                }

                # اگر از انبار انتخاب شده، اطلاعات انبار رو هم اضافه کن
                if warehouse_qty > 0 and warehouse_selection:
                    consumed_item['warehouse_details'] = {
                        'inventory_item_id': warehouse_selection.get('inventory_item_id'),
                        'warehouse_item_code': warehouse_selection.get('item_code', ''),
                        'warehouse_code': warehouse_selection.get('warehouse_code', ''),
                        'warehouse_name': warehouse_selection.get('warehouse_name', ''),
                        'warehouse_qty': warehouse_qty,
                        'direct_qty': direct_qty,
                        'match_type': warehouse_selection.get('match_type', 'MANUAL'),
                        'confidence_score': warehouse_selection.get('confidence_score', 1.0)
                    }

                consumed_items.append(consumed_item)

            # جمع‌آوری اطلاعات اسپول
            spool_data = getattr(self, 'spool_selections', {}).get(row_idx, [])
            if spool_data:
                for spool in spool_data:
                    # اطمینان از وجود فیلدهای مورد نیاز
                    spool_item = {
                        'spool_id': spool.get('spool_id'),
                        'mto_item_id': mto_item_id,
                        'used_qty': spool.get('used_qty', 0),
                        'item_code': item.get("Item Code", ""),
                        'description': item.get("Description", "")
                    }
                    spool_items.append(spool_item)

        return consumed_items, spool_items

    def auto_fill_from_warehouse(self):
        """پر کردن خودکار آیتم‌های MTO از انبار"""
        filled_items = []
        for row in range(self.table.rowCount()):
            mto_item_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not mto_item_id:
                continue

            # مقدار باقیمانده
            remaining_spin = self.table.cellWidget(row, 7)
            remaining_qty = remaining_spin.value() if remaining_spin else 0
            if remaining_qty <= 0:
                continue

            # بررسی موجودی با استفاده از Coordinator
            availability = self._check_warehouse_availability(mto_item_id)
            if not availability or not availability.get("available"):
                continue

            available_qty = availability.get("available_qty", 0)
            used_qty = min(remaining_qty, available_qty)

            if used_qty > 0:
                # ثبت مصرف از انبار در حافظه موقت
                filled_items.append({
                    "mto_item_id": mto_item_id,
                    "used_qty": used_qty,
                    "available_qty": available_qty,
                    "unit": self.table.item(row, 5).text(),
                    "filled_from_warehouse": True
                })

                # بروزرسانی جدول (کاهش باقیمانده)
                remaining_spin.setValue(max(0, remaining_qty - used_qty))

                # رنگ‌آمیزی برای نشان دادن پر شدن
                for col in range(self.table.columnCount()):
                    self.table.item(row, col).setBackground(QColor(220, 255, 220))  # سبز روشن

        if filled_items:
            QMessageBox.information(
                self, "تکمیل خودکار",
                f"{len(filled_items)} آیتم از انبار تکمیل شد."
            )
        else:
            QMessageBox.warning(self, "تکمیل خودکار", "هیچ آیتمی برای تکمیل از انبار یافت نشد.")

        self.auto_filled_items = filled_items

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
