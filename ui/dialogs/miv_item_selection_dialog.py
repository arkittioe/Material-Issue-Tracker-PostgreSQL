# ui/dialogs/miv_item_selection_dialog.py
"""
دیالوگ انتخاب هوشمند آیتم از انبار برای MIV
"""
from datetime import datetime
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
from data.consumption_service import ConsumptionService

logger = logging.getLogger(__name__)


class MIVItemSelectionDialog(QDialog):
    """دیالوگ انتخاب آیتم از انبار با قابلیت تطبیق هوشمند"""

    item_selected = pyqtSignal(dict)  # سیگنال برای ارسال آیتم انتخاب شده
    feedback_recorded = pyqtSignal(dict)  # سیگنال جدید برای ثبت feedback

    def __init__(self,
                 warehouse_service: WarehouseService,
                 item_matching_service: ItemMatchingService,
                 mto_item: Optional[MTOItem] = None,
                 consumption_service: Optional[ConsumptionService] = None,  # جدید
                 session_factory=None,  # جدید
                 parent=None):
        super().__init__(parent)
        self.warehouse_service = warehouse_service
        self.item_matching_service = item_matching_service
        self.mto_item = mto_item
        self.selected_item = None

        # جدید - اضافه کردن ConsumptionService
        if consumption_service:
            self.consumption_service = consumption_service
        elif session_factory:
            self.consumption_service = ConsumptionService(
                session_factory=session_factory,
                warehouse_service=warehouse_service
            )
        else:
            self.consumption_service = None
            logger.warning("No ConsumptionService available - feedback recording disabled")

        # مقادیر پیش‌فرض برای project و user
        self.current_project_id = None
        self.current_user = "operator"

        # دریافت از parent اگر موجود باشد
        if parent:
            self.current_project_id = getattr(parent, 'project_id', None)
            self.current_user = getattr(parent, 'current_user', 'operator')

        self.setWindowTitle("انتخاب آیتم از انبار")
        self.setModal(True)
        self.resize(1200, 700)

        self.setup_ui()
        self.load_initial_data()

    def setup_ui(self):
        """راه‌اندای رابط کاربری"""
        layout = QVBoxLayout()

        # بخش اطلاعات MTO (اگر وجود دارد)
        if self.mto_item:
            mto_group = QGroupBox("اطلاعات درخواست از MTO")
            mto_layout = QHBoxLayout()

            # بررسی وجود فیلدها با getattr برای جلوگیری از خطا
            item_code = getattr(self.mto_item, 'item_code', 'N/A')
            description = getattr(self.mto_item, 'description', 'N/A')
            # پشتیبانی از هر دو نام size و size_1
            size = getattr(self.mto_item, 'size', None) or getattr(self.mto_item, 'size_1', '-')
            qty = getattr(self.mto_item, 'qty', 0)

            mto_info = QLabel(
                f"کد: {item_code} | "
                f"شرح: {description} | "
                f"سایز: {size} | "
                f"مقدار درخواستی: {qty}"
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
        self.search_input.setPlaceholderText("کد آیتم یا شماره انبار...")
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

        search_row2.addWidget(QLabel("شرح:"))
        self.description_filter = QLineEdit()
        self.description_filter.setPlaceholderText("جستجو در شرح...")
        self.description_filter.textChanged.connect(self.filter_items)
        search_row2.addWidget(self.description_filter)

        search_row2.addWidget(QLabel("سایز:"))
        self.size_filter = QLineEdit()
        self.size_filter.setPlaceholderText("مثال: 2\"")
        self.size_filter.textChanged.connect(self.filter_items)
        search_row2.addWidget(self.size_filter)

        search_row2.addWidget(QLabel("نوع:"))
        self.type_filter = QComboBox()
        self.type_filter.addItems(["همه", "PIPE", "FITTING", "VALVE", "FLANGE", "GASKET", "BOLT"])
        self.type_filter.currentTextChanged.connect(self.filter_items)
        search_row2.addWidget(self.type_filter)

        search_layout.addLayout(search_row2)

        # 🔧 اضافه کردن status_label که گم شده بود!
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #666;
                font-size: 11px;
                padding: 5px;
                background-color: #f5f5f5;
                border-radius: 3px;
                margin-top: 5px;
            }
        """)
        search_layout.addWidget(self.status_label)

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
            "کد آیتم",
            "شماره آیتم انبار",
            "شرح",
            "سایز",
            "نوع",
            "موجودی",
            "واحد",
            "امتیاز تطبیق",
            "نوع تطبیق"
        ])
        self.smart_table.horizontalHeader().setStretchLastSection(True)
        self.smart_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.smart_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.smart_table.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.smart_table.itemSelectionChanged.connect(self.on_selection_changed)

        # تنظیم عرض ستون‌ها
        header = self.smart_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)

        smart_layout.addWidget(self.smart_table)
        smart_group.setLayout(smart_layout)
        splitter.addWidget(smart_group)

        # جدول همه موارد موجود در انبار
        inventory_group = QGroupBox("همه موارد موجود در انبار")
        inventory_layout = QVBoxLayout()

        self.inventory_table = QTableWidget()
        self.inventory_table.setColumnCount(9)
        self.inventory_table.setHorizontalHeaderLabels([
            "کد آیتم",
            "شماره آیتم انبار",
            "شرح",
            "سایز",
            "نوع",
            "موجودی فیزیکی",
            "رزرو شده",
            "قابل دسترس",
            "واحد"
        ])
        self.inventory_table.horizontalHeader().setStretchLastSection(True)
        self.inventory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.inventory_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.inventory_table.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.inventory_table.itemSelectionChanged.connect(self.on_selection_changed)

        # تنظیم عرض ستون‌ها
        header = self.inventory_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        inventory_layout.addWidget(self.inventory_table)
        inventory_group.setLayout(inventory_layout)
        splitter.addWidget(inventory_group)

        layout.addWidget(splitter)

        # بخش پایین - اطلاعات آیتم انتخاب شده
        selection_group = QGroupBox("آیتم انتخاب شده")
        selection_layout = QVBoxLayout()

        self.selected_info = QLabel("هیچ آیتمی انتخاب نشده")
        self.selected_info.setStyleSheet("padding: 10px; background-color: #f0f0f0; border-radius: 5px;")
        selection_layout.addWidget(self.selected_info)

        # بخش انتخاب مقدار
        qty_layout = QHBoxLayout()
        qty_layout.addWidget(QLabel("مقدار مورد نیاز:"))

        self.quantity_spin = QDoubleSpinBox()
        self.quantity_spin.setMinimum(0.01)
        self.quantity_spin.setMaximum(99999)
        self.quantity_spin.setDecimals(2)
        self.quantity_spin.setSingleStep(1)
        if self.mto_item:
            self.quantity_spin.setValue(getattr(self.mto_item, 'qty', 1))
        qty_layout.addWidget(self.quantity_spin)

        qty_layout.addStretch()
        selection_layout.addLayout(qty_layout)

        selection_group.setLayout(selection_layout)
        layout.addWidget(selection_group)

        # دکمه‌های انتخاب
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.select_btn = QPushButton("✓ انتخاب")
        self.select_btn.setEnabled(False)
        self.select_btn.clicked.connect(self.accept_selection)
        self.select_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 8px 20px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        button_layout.addWidget(self.select_btn)

        self.cancel_btn = QPushButton("✗ انصراف")
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                padding: 8px 20px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
        """)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)

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
        """بارگذاری پیشنهادات هوشمند با دیباگ کامل"""
        try:
            if not self.mto_item:
                logger.warning("No MTO item provided")
                return

            # استخراج اطلاعات MTO با لاگ
            mto_code = getattr(self.mto_item, 'item_code', None)
            mto_description = getattr(self.mto_item, 'description', None)
            mto_size = getattr(self.mto_item, 'size', None) or getattr(self.mto_item, 'size_1', None)
            mto_spec = getattr(self.mto_item, 'spec', None) or getattr(self.mto_item, 'type', None)

            # لاگ اطلاعات MTO
            logger.info(f"""
            MTO Item Details:
            - Code: {mto_code}
            - Description: {mto_description}
            - Size: {mto_size}
            - Spec: {mto_spec}
            """)

            # دریافت اطلاعات کاربر و پروژه از parent window
            project_id = None
            user_id = None

            if hasattr(self.parent(), 'project_id'):
                project_id = self.parent().project_id
                logger.info(f"Found project_id: {project_id}")
            else:
                logger.warning("No project_id found in parent")

            if hasattr(self.parent(), 'user_id'):
                user_id = self.parent().user_id
                logger.info(f"Found user_id: {user_id}")
            else:
                logger.warning("No user_id found in parent")

            # تبدیل به string اگر لازم باشد
            if mto_size is not None:
                mto_size = str(mto_size)
            if mto_spec is not None:
                mto_spec = str(mto_spec)

            # درخواست پیشنهادات هوشمند
            logger.info("Calling get_all_suggestions...")
            suggestions = self.item_matching_service.get_all_suggestions(
                mto_item_code=mto_code,
                mto_description=mto_description,
                mto_size=mto_size,
                mto_spec=mto_spec,
                project_id=project_id,
                user_id=user_id,
                top_n=20,
                min_confidence=0.3
            )

            logger.info(f"Received {len(suggestions) if suggestions else 0} suggestions")

            if suggestions:
                # لاگ اولین پیشنهاد برای بررسی
                logger.info(f"First suggestion: {suggestions[0]}")

            if not suggestions:
                self.status_label.setText("⚠️ پیشنهاد مناسبی یافت نشد - نمایش همه موارد موجود")
                self.status_label.setStyleSheet("""
                    QLabel {
                        color: #FF9800;
                        font-size: 11px;
                        padding: 5px;
                        background-color: #FFF3E0;
                        border-radius: 3px;
                    }
                """)
                # نمایش تمام آیتم‌های موجود
                self.search_inventory()
                return

            # پاک کردن جدول قبلی
            self.smart_table.setRowCount(0)

            # نمایش پیشنهادات
            for row, suggestion in enumerate(suggestions):
                logger.debug(f"Processing suggestion {row}: {suggestion}")

                # ستون‌های جدول را پر کنیم
                self.smart_table.insertRow(row)

                # استخراج داده‌ها
                item_code = suggestion.get('item_code', '')
                warehouse_item_number = suggestion.get('warehouse_item_number', '')
                description = suggestion.get('description', '')
                size = suggestion.get('size', '')
                item_type = suggestion.get('type', '')
                available_qty = suggestion.get('available_qty', 0)
                unit = suggestion.get('unit', 'EA')
                confidence = suggestion.get('confidence', 0) * 100
                match_source = suggestion.get('match_source', 'UNKNOWN')
                match_reason = suggestion.get('match_reason', '')

                # تعیین نوع و رنگ
                if match_source == 'EXACT':
                    match_display = "✅ تطابق کامل"
                    color = QColor(0, 150, 0)
                elif match_source == 'RULE':
                    match_display = f"📋 {match_reason or 'قانون‌محور'}"
                    color = QColor(100, 200, 100)
                elif match_source == 'NLP':
                    match_display = f"🤖 {match_reason or 'شباهت معنایی'}"
                    color = QColor(255, 165, 0)
                else:
                    match_display = "❓ دستی"
                    color = QColor(200, 200, 200)

                # پر کردن ستون‌ها
                item_widget = QTableWidgetItem(str(item_code))
                item_widget.setData(Qt.ItemDataRole.UserRole, suggestion)
                self.smart_table.setItem(row, 0, item_widget)

                self.smart_table.setItem(row, 1, QTableWidgetItem(str(warehouse_item_number)))
                self.smart_table.setItem(row, 2, QTableWidgetItem(str(description)))
                self.smart_table.setItem(row, 3, QTableWidgetItem(str(size)))
                self.smart_table.setItem(row, 4, QTableWidgetItem(str(item_type)))

                # موجودی با رنگ‌بندی
                qty_item = QTableWidgetItem(f"{available_qty:.2f}")
                if available_qty <= 0:
                    qty_item.setForeground(QColor("red"))
                elif available_qty < 10:
                    qty_item.setForeground(QColor("orange"))
                else:
                    qty_item.setForeground(QColor("green"))
                self.smart_table.setItem(row, 5, qty_item)

                self.smart_table.setItem(row, 6, QTableWidgetItem(str(unit)))

                # امتیاز
                score_item = QTableWidgetItem(f"{confidence:.0f}%")
                score_item.setForeground(color)
                self.smart_table.setItem(row, 7, score_item)

                # نوع تطابق
                match_item = QTableWidgetItem(match_display)
                match_item.setForeground(color)
                self.smart_table.setItem(row, 8, match_item)

            # تنظیم عرض ستون‌ها
            self.smart_table.resizeColumnsToContents()

            # به‌روزرسانی وضعیت
            status_text = f"✅ {len(suggestions)} پیشنهاد یافت شد"
            self.status_label.setText(status_text)
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #4CAF50;
                    font-size: 11px;
                    padding: 5px;
                    background-color: #E8F5E9;
                    border-radius: 3px;
                }
            """)

        except Exception as e:
            logger.error(f"خطا در بارگذاری پیشنهادات هوشمند: {e}", exc_info=True)
            self.status_label.setText(f"❌ خطا: {str(e)}")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #f44336;
                    font-size: 11px;
                    padding: 5px;
                    background-color: #FFEBEE;
                    border-radius: 3px;
                }
            """)
            # نمایش لیست عادی
            self.search_inventory()

    def _fill_smart_suggestion_row(self, row, item, suggestion_data, confidence, match_type, row_color=None):
        """پر کردن یک ردیف از جدول پیشنهادات با داده‌های ترکیبی"""

        # استفاده از داده‌های suggestion_data که از get_all_suggestions آمده
        item_code = suggestion_data.get('item_code', getattr(item, 'item_code', ''))
        warehouse_item_number = suggestion_data.get('warehouse_item_number', getattr(item, 'warehouse_item_number', ''))
        description = suggestion_data.get('description', getattr(item, 'description', ''))
        size = suggestion_data.get('size', getattr(item, 'size', ''))
        item_type = suggestion_data.get('type', getattr(item, 'type', ''))
        available_qty = suggestion_data.get('available_qty', getattr(item, 'available_qty', 0))
        unit = suggestion_data.get('unit', getattr(item, 'unit', 'EA'))

        # ستون 0: کد آیتم
        code_item = QTableWidgetItem(str(item_code))
        self.smart_table.setItem(row, 0, code_item)

        # ستون 1: شماره آیتم انبار
        self.smart_table.setItem(row, 1, QTableWidgetItem(str(warehouse_item_number or '')))

        # ستون 2: شرح
        self.smart_table.setItem(row, 2, QTableWidgetItem(str(description)))

        # ستون 3: سایز
        self.smart_table.setItem(row, 3, QTableWidgetItem(str(size or '')))

        # ستون 4: نوع
        self.smart_table.setItem(row, 4, QTableWidgetItem(str(item_type or '')))

        # ستون 5: موجودی
        qty_item = QTableWidgetItem(f"{available_qty:.2f}")
        if available_qty <= 0:
            qty_item.setForeground(QColor("red"))
        elif available_qty < 10:
            qty_item.setForeground(QColor("orange"))
        else:
            qty_item.setForeground(QColor("green"))
        self.smart_table.setItem(row, 5, qty_item)

        # ستون 6: واحد
        self.smart_table.setItem(row, 6, QTableWidgetItem(str(unit)))

        # ستون 7: امتیاز اطمینان
        score_item = QTableWidgetItem(f"{confidence:.0f}%")
        if confidence >= 90:
            score_item.setForeground(QColor("darkGreen"))
            score_item.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        elif confidence >= 70:
            score_item.setForeground(QColor("green"))
        elif confidence >= 50:
            score_item.setForeground(QColor("orange"))
        else:
            score_item.setForeground(QColor("red"))
        self.smart_table.setItem(row, 7, score_item)

        # ستون 8: نوع تطابق (ترکیب منبع و دلیل)
        match_item = QTableWidgetItem(match_type)
        self.smart_table.setItem(row, 8, match_item)

        # رنگ‌آمیزی ردیف بر اساس نوع تطابق
        if row_color:
            for col in range(self.smart_table.columnCount()):
                item = self.smart_table.item(row, col)
                if item:
                    item.setBackground(row_color)

        # ذخیره کل داده‌ها در UserRole برای استفاده بعدی
        self.smart_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, {
            'item': item,  # شیء ORM
            'suggestion_data': suggestion_data,  # داده‌های کامل از get_all_suggestions
            'confidence': confidence / 100,  # تبدیل به 0-1
            'match_source': suggestion_data.get('match_source', 'UNKNOWN')
        })

    def load_all_inventory(self):
        """بارگذاری همه موجودی‌های انبار"""
        try:
            warehouse_id = self.warehouse_combo.currentData()

            # استفاده از متد get_inventory_items که در WarehouseService موجود است
            filters = {}
            if warehouse_id:
                filters['warehouse_id'] = warehouse_id

            items = self.warehouse_service.get_inventory_items(filters)

            self.inventory_table.setRowCount(0)

            for item in items:
                if not item:
                    continue

                row = self.inventory_table.rowCount()
                self.inventory_table.insertRow(row)

                # ستون 0: کد آیتم
                self.inventory_table.setItem(row, 0, QTableWidgetItem(str(item.item_code or '')))

                # ستون 1: شماره آیتم انبار
                self.inventory_table.setItem(row, 1, QTableWidgetItem(str(item.warehouse_item_number or '')))

                # ستون 2: شرح
                self.inventory_table.setItem(row, 2, QTableWidgetItem(str(item.description or '')))

                # ستون 3: سایز
                self.inventory_table.setItem(row, 3, QTableWidgetItem(str(item.size or '')))

                # ستون 4: نوع
                self.inventory_table.setItem(row, 4, QTableWidgetItem(str(item.type or '')))

                # ستون 5: موجودی فیزیکی
                physical_qty = getattr(item, 'physical_qty', 0) or 0
                self.inventory_table.setItem(row, 5, QTableWidgetItem(f"{physical_qty:.2f}"))

                # ستون 6: رزرو شده
                reserved_qty = getattr(item, 'reserved_qty', 0) or 0
                self.inventory_table.setItem(row, 6, QTableWidgetItem(f"{reserved_qty:.2f}"))

                # ستون 7: قابل دسترس
                available_qty = getattr(item, 'available_qty', 0) or 0
                qty_item = QTableWidgetItem(f"{available_qty:.2f}")
                if available_qty <= 0:
                    qty_item.setForeground(QColor("red"))
                self.inventory_table.setItem(row, 7, qty_item)

                # ستون 8: واحد
                self.inventory_table.setItem(row, 8, QTableWidgetItem(str(item.unit or '')))

                # ذخیره آبجکت کامل در UserRole
                self.inventory_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, item)

        except Exception as e:
            logger.error(f"خطا در بارگذاری موجودی‌ها: {e}")

    def setup_filters(self):
        """ایجاد فیلترهای جستجو"""
        filter_layout = QHBoxLayout()

        # فیلتر عمومی (کد آیتم + شماره انبار)
        filter_layout.addWidget(QLabel("جستجو:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("کد آیتم یا شماره انبار...")
        self.search_input.textChanged.connect(self.filter_items)
        filter_layout.addWidget(self.search_input)

        # فیلتر توضیحات (جدید)
        filter_layout.addWidget(QLabel("شرح:"))
        self.description_filter = QLineEdit()
        self.description_filter.setPlaceholderText("جستجو در شرح...")
        self.description_filter.textChanged.connect(self.filter_items)
        filter_layout.addWidget(self.description_filter)

        # فیلتر سایز
        filter_layout.addWidget(QLabel("سایز:"))
        self.size_filter = QLineEdit()
        self.size_filter.setPlaceholderText("فیلتر سایز...")
        self.size_filter.textChanged.connect(self.filter_items)
        filter_layout.addWidget(self.size_filter)

        # فیلتر نوع (ComboBox)
        filter_layout.addWidget(QLabel("نوع:"))
        self.type_filter = QComboBox()
        self.type_filter.addItems(["همه", "PIPE", "FITTING", "VALVE", "FLANGE", "GASKET", "BOLT"])
        self.type_filter.currentTextChanged.connect(self.filter_items)
        filter_layout.addWidget(self.type_filter)

        filter_layout.addStretch()
        return filter_layout

    def setup_smart_table(self):
        """ایجاد جدول پیشنهادات هوشمند"""
        self.smart_table = QTableWidget()
        self.smart_table.setColumnCount(9)
        self.smart_table.setHorizontalHeaderLabels([
            "کد آیتم",
            "شماره آیتم انبار",
            "شرح",
            "سایز",
            "نوع",
            "موجودی",
            "واحد",
            "امتیاز تطبیق",
            "نوع تطبیق"
        ])
        self.smart_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.smart_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.smart_table.itemSelectionChanged.connect(self.on_smart_selection_changed)
        self.smart_table.horizontalHeader().setStretchLastSection(True)
        return self.smart_table

    def setup_inventory_table(self):
        """ایجاد جدول موجودی انبار"""
        self.inventory_table = QTableWidget()
        self.inventory_table.setColumnCount(9)
        self.inventory_table.setHorizontalHeaderLabels([
            "کد آیتم",
            "شماره آیتم انبار",
            "شرح",
            "سایز",
            "نوع",
            "موجودی فیزیکی",
            "رزرو شده",
            "قابل دسترس",
            "واحد"
        ])
        self.inventory_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.inventory_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.inventory_table.itemSelectionChanged.connect(self.on_inventory_selection_changed)
        self.inventory_table.horizontalHeader().setStretchLastSection(True)
        return self.inventory_table

    def filter_items(self):
        """فیلتر کردن آیتم‌های جدول بر اساس فیلترها"""
        search_text = self.search_input.text().lower()
        description_text = self.description_filter.text().lower()
        size_text = self.size_filter.text().lower()
        type_text = self.type_filter.currentText()

        # فیلتر برای جدول هوشمند
        for row in range(self.smart_table.rowCount()):
            show_row = True

            # جستجو در کد آیتم و شماره انبار
            if search_text:
                item_code = self.smart_table.item(row, 0).text().lower() if self.smart_table.item(row, 0) else ""
                warehouse_num = self.smart_table.item(row, 1).text().lower() if self.smart_table.item(row, 1) else ""
                if search_text not in item_code and search_text not in warehouse_num:
                    show_row = False

            # فیلتر توضیحات
            if description_text and description_text not in (
                    self.smart_table.item(row, 2).text().lower() if self.smart_table.item(row, 2) else ""):
                show_row = False

            # فیلتر سایز
            if size_text and size_text not in (
                    self.smart_table.item(row, 3).text().lower() if self.smart_table.item(row, 3) else ""):
                show_row = False

            # فیلتر نوع
            if type_text != "همه":
                item_type = self.smart_table.item(row, 4).text() if self.smart_table.item(row, 4) else ""
                if type_text != item_type:
                    show_row = False

            self.smart_table.setRowHidden(row, not show_row)

        # فیلتر برای جدول موجودی
        for row in range(self.inventory_table.rowCount()):
            show_row = True

            # جستجو در کد آیتم و شماره انبار
            if search_text:
                item_code = self.inventory_table.item(row, 0).text().lower() if self.inventory_table.item(row,
                                                                                                          0) else ""
                warehouse_num = self.inventory_table.item(row, 1).text().lower() if self.inventory_table.item(row,
                                                                                                              1) else ""
                if search_text not in item_code and search_text not in warehouse_num:
                    show_row = False

            # فیلتر توضیحات
            if description_text and description_text not in (
                    self.inventory_table.item(row, 2).text().lower() if self.inventory_table.item(row, 2) else ""):
                show_row = False

            # فیلتر سایز
            if size_text and size_text not in (
                    self.inventory_table.item(row, 3).text().lower() if self.inventory_table.item(row, 3) else ""):
                show_row = False

            # فیلتر نوع
            if type_text != "همه":
                item_type = self.inventory_table.item(row, 4).text() if self.inventory_table.item(row, 4) else ""
                if type_text != item_type:
                    show_row = False

            self.inventory_table.setRowHidden(row, not show_row)

    def perform_search(self):
        """اجرای جستجوی دستی"""
        try:
            search_text = self.search_input.text().strip()

            # ساخت فیلترها به صورت ساده (نه دیکشنری تودرتو)
            filters = {}

            if search_text:
                filters['search_text'] = search_text

            # فیلتر انبار
            warehouse_code = self.warehouse_combo.currentData()
            if warehouse_code:
                # پیدا کردن warehouse_id از کد انبار
                warehouses = self.warehouse_service.get_all_warehouses()
                for wh in warehouses:
                    if wh.code == warehouse_code:
                        filters['warehouse_id'] = wh.id
                        break

            # فیلتر نوع
            if hasattr(self, 'type_filter'):
                type_text = self.type_filter.currentText()
                if type_text and type_text != "همه":
                    filters['type'] = type_text

            # فیلتر توضیحات
            if hasattr(self, 'description_filter'):
                desc_text = self.description_filter.text().strip()
                if desc_text:
                    filters['description'] = desc_text

            # جستجو در انبار با استفاده از warehouse_service
            items = self.warehouse_service.get_inventory_items(filters)

            # نمایش در جدول
            self.inventory_table.setRowCount(len(items))

            for row, item in enumerate(items):
                # ستون 0: کد کالا
                item_widget = QTableWidgetItem(str(item.item_code or ''))
                item_widget.setData(Qt.ItemDataRole.UserRole, item)
                self.inventory_table.setItem(row, 0, item_widget)

                # ستون 1: شماره قطعه انبار
                self.inventory_table.setItem(row, 1,
                                             QTableWidgetItem(str(item.warehouse_item_number or '')))

                # ستون 2: توضیحات
                self.inventory_table.setItem(row, 2,
                                             QTableWidgetItem(str(item.description or '')))

                # ستون 3: سایز
                self.inventory_table.setItem(row, 3,
                                             QTableWidgetItem(str(item.size or '')))

                # ستون 4: نوع
                self.inventory_table.setItem(row, 4,
                                             QTableWidgetItem(str(item.type or '')))

                # ستون 5: موجودی
                available_qty = getattr(item, 'available_qty', 0) or 0
                reserved_qty = getattr(item, 'reserved_qty', 0) or 0
                qty_text = f"{available_qty:.2f}"
                if reserved_qty > 0:
                    qty_text += f" (رزرو: {reserved_qty:.2f})"

                qty_item = QTableWidgetItem(qty_text)
                if available_qty <= 0:
                    qty_item.setForeground(QColor("red"))
                elif available_qty < 10:
                    qty_item.setForeground(QColor("orange"))
                else:
                    qty_item.setForeground(QColor("green"))
                self.inventory_table.setItem(row, 5, qty_item)

                # ستون 6: واحد
                self.inventory_table.setItem(row, 6,
                                             QTableWidgetItem(str(item.unit or 'EA')))

                # ستون 7: انبار
                warehouse_name = ""
                # دسترسی ایمن به warehouse
                try:
                    if hasattr(item, 'warehouse_id') and item.warehouse_id:
                        # اگر warehouse loaded نشده، از warehouse_service استفاده کن
                        warehouses = self.warehouse_service.get_all_warehouses()
                        for wh in warehouses:
                            if wh.id == item.warehouse_id:
                                warehouse_name = wh.name or wh.code
                                break
                except:
                    warehouse_name = f"انبار {item.warehouse_id}" if hasattr(item, 'warehouse_id') else ""

                self.inventory_table.setItem(row, 7,
                                             QTableWidgetItem(warehouse_name))

                # ستون 8: تاریخ آخرین تراکنش (اصلاح شده)
                last_date = ""
                if hasattr(item, 'last_issue_date') and item.last_issue_date:
                    last_date = f"صدور: {item.last_issue_date.strftime('%Y-%m-%d')}"
                elif hasattr(item, 'last_receipt_date') and item.last_receipt_date:
                    last_date = f"ورود: {item.last_receipt_date.strftime('%Y-%m-%d')}"

                self.inventory_table.setItem(row, 8, QTableWidgetItem(last_date))

            # نمایش تعداد نتایج
            if hasattr(self, 'status_label'):
                self.status_label.setText(f"تعداد {len(items)} آیتم یافت شد")

        except Exception as e:
            logger.error(f"Error in warehouse search: {e}")
            QMessageBox.warning(
                self,
                "خطا در جستجو",
                f"خطا در جستجوی انبار:\n{str(e)}"
            )

    def _fill_smart_table_row_from_dict(self, row, item_data, confidence, match_type):
        """پر کردن یک سطر جدول هوشمند از دیکشنری"""
        self.smart_table.setItem(row, 0, QTableWidgetItem(str(item_data.get('item_code', ''))))
        self.smart_table.setItem(row, 1, QTableWidgetItem(str(item_data.get('warehouse_item_number', ''))))
        self.smart_table.setItem(row, 2, QTableWidgetItem(str(item_data.get('description', ''))))
        self.smart_table.setItem(row, 3, QTableWidgetItem(str(item_data.get('size', ''))))
        self.smart_table.setItem(row, 4, QTableWidgetItem(str(item_data.get('type', ''))))

        available_qty = item_data.get('available_qty', 0)
        qty_item = QTableWidgetItem(f"{available_qty:.2f}")
        if available_qty <= 0:
            qty_item.setForeground(QColor("red"))
        self.smart_table.setItem(row, 5, qty_item)

        self.smart_table.setItem(row, 6, QTableWidgetItem(str(item_data.get('unit', ''))))

        score_item = QTableWidgetItem(f"{confidence:.0f}%")
        if confidence >= 80:
            score_item.setForeground(QColor("green"))
        elif confidence >= 60:
            score_item.setForeground(QColor("orange"))
        else:
            score_item.setForeground(QColor("red"))
        self.smart_table.setItem(row, 7, score_item)

        self.smart_table.setItem(row, 8, QTableWidgetItem(match_type))

        # ذخیره داده کامل
        self.smart_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, item_data)

    def _fill_inventory_table_row(self, row, item):
        """پر کردن یک سطر جدول موجودی"""
        # حذف دسترسی به last_transaction_date که وجود ندارد
        self.inventory_table.setItem(row, 0, QTableWidgetItem(str(getattr(item, 'item_code', ''))))
        self.inventory_table.setItem(row, 1, QTableWidgetItem(str(getattr(item, 'warehouse_item_number', ''))))
        self.inventory_table.setItem(row, 2, QTableWidgetItem(str(getattr(item, 'description', ''))))
        self.inventory_table.setItem(row, 3, QTableWidgetItem(str(getattr(item, 'size', ''))))
        self.inventory_table.setItem(row, 4, QTableWidgetItem(str(getattr(item, 'type', ''))))

        available_qty = getattr(item, 'available_qty', 0)
        qty_item = QTableWidgetItem(f"{available_qty:.2f}")
        if available_qty <= 0:
            qty_item.setForeground(QColor("red"))
        self.inventory_table.setItem(row, 5, qty_item)

        self.inventory_table.setItem(row, 6, QTableWidgetItem(str(getattr(item, 'unit', ''))))

        # آخرین تاریخ - استفاده از فیلدهای موجود
        last_date = getattr(item, 'last_issue_date', None) or getattr(item, 'last_receipt_date', None)
        if last_date:
            self.inventory_table.setItem(row, 7, QTableWidgetItem(last_date.strftime('%Y-%m-%d')))
        else:
            self.inventory_table.setItem(row, 7, QTableWidgetItem('-'))

        # ذخیره داده کامل
        self.inventory_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, item)

    def _show_smart_matches(self, items):
        """نمایش موارد منطبق در جدول پیشنهادات هوشمند"""
        mto_code = getattr(self.mto_item, 'item_code', '').lower()
        mto_desc = getattr(self.mto_item, 'description', '').lower()
        mto_size = (getattr(self.mto_item, 'size', None) or getattr(self.mto_item, 'size_1', '')).lower()

        matched_items = []

        for item in items:
            score = 0
            match_type = "manual"

            item_code = (getattr(item, 'item_code', '') or '').lower()
            item_desc = (getattr(item, 'description', '') or '').lower()
            item_size = (getattr(item, 'size', '') or '').lower()

            # محاسبه امتیاز
            if item_code and mto_code:
                if item_code == mto_code:
                    score += 50
                    match_type = "exact"
                elif mto_code in item_code or item_code in mto_code:
                    score += 30
                    match_type = "partial"

            if item_desc and mto_desc:
                common_words = set(mto_desc.split()) & set(item_desc.split())
                if common_words:
                    score += min(30, len(common_words) * 10)
                    if match_type == "manual":
                        match_type = "description"

            if item_size and mto_size and item_size == mto_size:
                score += 20
                if match_type == "manual":
                    match_type = "size"

            if score > 0:
                matched_items.append((item, score, match_type))

        # مرتب‌سازی و نمایش
        matched_items.sort(key=lambda x: x[1], reverse=True)

        for item, score, match_type in matched_items[:10]:
            row = self.smart_table.rowCount()
            self.smart_table.insertRow(row)
            self._fill_smart_table_row(row, item, score, match_type)

    def on_item_double_clicked(self, item):
        """دابل کلیک برای انتخاب سریع"""
        # دریافت داده‌های آیتم
        row = item.row()
        table = item.tableWidget()

        # استخراج داده‌ها از ردیف
        item_data = {}
        if table == self.smart_table:
            # از جدول پیشنهادات هوشمند
            item_widget = table.item(row, 0)
            if item_widget:
                item_data = item_widget.data(Qt.ItemDataRole.UserRole) or {}

        if not item_data:
            # استخراج دستی از جدول
            item_data = self._extract_row_data(table, row)

        self.selected_item = item_data
        self.accept_selection()

    def on_selection_changed(self):
        """هنگام تغییر انتخاب در جداول"""
        # تشخیص جدول فعال
        if self.smart_table.selectedItems():
            current_table = self.smart_table
        elif self.inventory_table.selectedItems():
            current_table = self.inventory_table
        else:
            self.selected_item = None
            self.selected_info.setText("هیچ آیتمی انتخاب نشده")
            self.select_btn.setEnabled(False)
            return

        # دریافت ردیف انتخاب شده
        current_row = current_table.currentRow()
        if current_row < 0:
            return

        # استخراج داده از UserRole
        first_item = current_table.item(current_row, 0)
        if not first_item:
            return

        item_data = first_item.data(Qt.ItemDataRole.UserRole)

        if isinstance(item_data, dict):
            # برای پیشنهادات هوشمند
            self.selected_item = item_data.get('suggestion_data', item_data)

            # نمایش اطلاعات
            item_code = self.selected_item.get('item_code', '')
            warehouse_code = self.selected_item.get('warehouse_code', '')
            description = self.selected_item.get('description', '')
            available = self.selected_item.get('available_qty', 0)
            unit = self.selected_item.get('unit', 'EA')

            info_text = (
                f"کد: {item_code} | "
                f"انبار: {warehouse_code} | "
                f"شرح: {description}\n"
                f"موجودی: {available:.2f} {unit}"
            )

            # اگر امتیاز تطبیق دارد، نمایش بده
            if 'confidence' in self.selected_item:
                confidence = self.selected_item['confidence'] * 100
                info_text += f" | امتیاز: {confidence:.0f}%"

        else:
            # برای آیتم‌های ORM
            self.selected_item = item_data

            item_code = getattr(item_data, 'item_code', '')
            warehouse_num = getattr(item_data, 'warehouse_item_number', '')
            description = getattr(item_data, 'description', '')
            available = getattr(item_data, 'available_qty', 0)
            unit = getattr(item_data, 'unit', 'EA')

            info_text = (
                f"کد: {item_code} | "
                f"شماره انبار: {warehouse_num} | "
                f"شرح: {description}\n"
                f"موجودی: {available:.2f} {unit}"
            )

        self.selected_info.setText(info_text)
        self.select_btn.setEnabled(True)

    def search_items(self):
        """جستجوی آیتم‌ها - سازگار با هر دو حالت"""
        try:
            # اگر متد perform_search وجود دارد، از آن استفاده کن
            if hasattr(self, 'perform_search'):
                self.perform_search()
                return

            # در غیر این صورت، جستجوی ساده انجام بده
            search_text = self.search_input.text().strip()
            if len(search_text) < 2:
                return

            # تعیین جدول هدف
            target_table = None
            if hasattr(self, 'inventory_table'):
                target_table = self.inventory_table
            elif hasattr(self, 'smart_table'):
                target_table = self.smart_table
            else:
                logger.warning("No suitable table found for search results")
                return

            # جستجو در انبار
            filters = {'search_text': search_text}

            # اضافه کردن فیلتر انبار اگر وجود دارد
            if hasattr(self, 'warehouse_combo'):
                warehouse_data = self.warehouse_combo.currentData()
                if warehouse_data:
                    filters['warehouse_id'] = warehouse_data

            items = self.warehouse_service.get_inventory_items(filters)

            # نمایش نتایج
            target_table.setRowCount(len(items))

            for row, item in enumerate(items):
                # ستون 0: کد کالا
                item_widget = QTableWidgetItem(str(item.item_code or ''))
                item_widget.setData(Qt.ItemDataRole.UserRole, item)
                target_table.setItem(row, 0, item_widget)

                # سایر ستون‌ها
                if target_table.columnCount() > 1:
                    target_table.setItem(row, 1,
                                         QTableWidgetItem(str(item.description or '')))

                if target_table.columnCount() > 2:
                    target_table.setItem(row, 2,
                                         QTableWidgetItem(str(item.type or '')))

                if target_table.columnCount() > 3:
                    target_table.setItem(row, 3,
                                         QTableWidgetItem(str(item.size or '')))

                if target_table.columnCount() > 4:
                    available_qty = getattr(item, 'available_qty', 0) or 0
                    qty_item = QTableWidgetItem(f"{available_qty:.2f}")
                    if available_qty <= 0:
                        qty_item.setForeground(QColor("red"))
                    target_table.setItem(row, 4, qty_item)

        except Exception as e:
            logger.error(f"Error in search_items: {e}")
            QMessageBox.warning(self, "خطا", f"خطا در جستجو:\n{str(e)}")

    def accept(self):
        """پذیرش انتخاب و آماده‌سازی داده‌ها"""
        try:
            # تعیین جدول فعال
            current_table = None

            # اولویت با smart_table است اگر آیتمی انتخاب شده باشد
            if hasattr(self, 'smart_table') and self.smart_table.currentRow() >= 0:
                current_table = self.smart_table
            elif hasattr(self, 'inventory_table') and self.inventory_table.currentRow() >= 0:
                current_table = self.inventory_table

            if not current_table:
                QMessageBox.warning(self, "هشدار", "لطفاً یک آیتم انتخاب کنید")
                return

            # دریافت آیتم انتخابی
            current_row = current_table.currentRow()
            first_item = current_table.item(current_row, 0)

            if not first_item:
                QMessageBox.warning(self, "خطا", "آیتم انتخابی معتبر نیست")
                return

            selected_item = first_item.data(Qt.ItemDataRole.UserRole)
            if not selected_item:
                QMessageBox.warning(self, "خطا", "داده آیتم انتخابی یافت نشد")
                return

            # دریافت مقدار
            required_qty = self.quantity_spin.value()
            if required_qty <= 0:
                QMessageBox.warning(self, "هشدار", "مقدار باید بیشتر از صفر باشد")
                return

            # 🆕 ثبت انتخاب کاربر برای یادگیری (قبل از بررسی موجودی)
            self._record_material_selection(selected_item, required_qty, current_table)

            # استخراج اطلاعات بر اساس نوع داده
            if isinstance(selected_item, dict):
                # داده از پیشنهادات هوشمند
                available = selected_item.get('available_qty', 0)

                # بررسی موجودی
                if required_qty > available:
                    reply = QMessageBox.question(
                        self,
                        "موجودی ناکافی",
                        f"موجودی قابل دسترس: {available:.2f}\n"
                        f"مقدار درخواستی: {required_qty:.2f}\n\n"
                        "آیا می‌خواهید ادامه دهید؟",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.No:
                        return

                # ساخت دیکشنری خروجی از داده dict
                self.selected_items = [{
                    'inventory_item_id': selected_item.get('item_id'),
                    'item_code': selected_item.get('item_code', ''),
                    'warehouse_item_number': selected_item.get('warehouse_item_number', ''),
                    'description': selected_item.get('description', ''),
                    'type': selected_item.get('type', ''),
                    'size': selected_item.get('size', ''),
                    'warehouse_code': selected_item.get('warehouse_code', ''),
                    'warehouse_id': selected_item.get('warehouse_id'),
                    'warehouse_name': selected_item.get('warehouse_name', ''),
                    'mto_item_id': self.mto_item.id if self.mto_item else None,
                    'used_qty': required_qty,
                    'available_qty': available,
                    'reserved_qty': selected_item.get('reserved_qty', 0),
                    'unit': selected_item.get('unit', 'EA'),
                    'confidence_score': selected_item.get('confidence', 1.0),
                    'match_type': selected_item.get('match_source', 'MANUAL')
                }]

            else:
                # داده از موجودی انبار (ORM Object)
                available = getattr(selected_item, 'available_qty', 0)

                # بررسی موجودی
                if required_qty > available:
                    reply = QMessageBox.question(
                        self,
                        "موجودی ناکافی",
                        f"موجودی قابل دسترس: {available:.2f}\n"
                        f"مقدار درخواستی: {required_qty:.2f}\n\n"
                        "آیا می‌خواهید ادامه دهید؟",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.No:
                        return

                # استخراج اطلاعات انبار به صورت ایمن
                warehouse_code = ''
                warehouse_name = ''
                warehouse_id = getattr(selected_item, 'warehouse_id', None)

                # تلاش برای دریافت اطلاعات انبار از warehouse_service
                if warehouse_id:
                    try:
                        warehouses = self.warehouse_service.get_all_warehouses()
                        for wh in warehouses:
                            if wh.id == warehouse_id:
                                warehouse_code = wh.code
                                warehouse_name = wh.name or wh.code
                                break
                    except:
                        warehouse_code = f"WH{warehouse_id}"
                        warehouse_name = f"انبار {warehouse_id}"

                # ساخت دیکشنری خروجی
                self.selected_items = [{
                    'inventory_item_id': selected_item.id if hasattr(selected_item, 'id') else None,
                    'item_code': getattr(selected_item, 'item_code', ''),
                    'warehouse_item_number': getattr(selected_item, 'warehouse_item_number', ''),
                    'description': getattr(selected_item, 'description', ''),
                    'type': getattr(selected_item, 'type', ''),
                    'size': getattr(selected_item, 'size', ''),
                    'warehouse_code': warehouse_code,
                    'warehouse_id': warehouse_id,
                    'warehouse_name': warehouse_name,
                    'mto_item_id': self.mto_item.id if self.mto_item else None,
                    'used_qty': required_qty,
                    'available_qty': available,
                    'reserved_qty': getattr(selected_item, 'reserved_qty', 0),
                    'unit': getattr(selected_item, 'unit', 'EA'),
                    'confidence_score': 1.0,
                    'match_type': 'MANUAL'
                }]

            # بستن دیالوگ
            super().accept()

        except Exception as e:
            logger.error(f"خطا در تأیید انتخاب: {e}")
            QMessageBox.critical(
                self,
                "خطای سیستم",
                f"خطا در پردازش انتخاب:\n{str(e)}"
            )

    def _record_material_selection(self, selected_item, required_qty, source_table):
        """
        ثبت انتخاب کاربر برای یادگیری خودکار
        """
        try:
            # استخراج کد آیتم انتخاب شده
            if isinstance(selected_item, dict):
                selected_code = selected_item.get('item_code', '')
                selected_item_id = selected_item.get('item_id', None)
                confidence = selected_item.get('confidence', 0.8)
                match_source = selected_item.get('match_source', 'UNKNOWN')
            else:
                selected_code = getattr(selected_item, 'item_code', '')
                selected_item_id = getattr(selected_item, 'id', None)
                confidence = 1.0
                match_source = 'MANUAL'

            if not selected_code or not self.mto_item:
                return

            # استخراج اطلاعات MTO
            mto_code = getattr(self.mto_item, 'item_code', '')
            mto_description = getattr(self.mto_item, 'description', '')

            # دریافت user_id از parent یا مقدار پیش‌فرض
            user_id = 'operator'
            project_id = None

            if hasattr(self.parent(), 'current_user'):
                user_id = self.parent().current_user
            if hasattr(self.parent(), 'project_id'):
                project_id = self.parent().project_id

            # فراخوانی صحیح بدون search_query
            success = self.item_matching_service.record_user_selection(
                mto_item_code=mto_code,
                mto_description=mto_description,
                selected_item_code=selected_code,
                selected_item_id=selected_item_id,
                match_source=match_source,
                confidence_at_selection=confidence,
                user_id=user_id,
                project_id=project_id
            )

            if success:
                logger.info(f"User selection recorded: {mto_code} -> {selected_code}")

        except Exception as e:
            # فقط لاگ کنیم، ارور رو به کاربر نشون ندیم
            logger.warning(f"Could not record selection for learning: {e}")

    def _fill_smart_table_row(self, row, item, score=0, match_type="manual"):
        """پر کردن یک ردیف از جدول پیشنهادات هوشمند"""
        # ستون 0: کد آیتم
        self.smart_table.setItem(row, 0, QTableWidgetItem(str(item.item_code or '')))

        # ستون 1: شماره آیتم انبار
        self.smart_table.setItem(row, 1, QTableWidgetItem(str(item.warehouse_item_number or '')))

        # ستون 2: شرح
        self.smart_table.setItem(row, 2, QTableWidgetItem(str(item.description or '')))

        # ستون 3: سایز
        self.smart_table.setItem(row, 3, QTableWidgetItem(str(item.size or '')))

        # ستون 4: نوع
        self.smart_table.setItem(row, 4, QTableWidgetItem(str(item.type or '')))

        # ستون 5: موجودی
        qty = getattr(item, 'available_qty', 0) or 0
        qty_item = QTableWidgetItem(f"{qty:.2f}")
        if qty <= 0:
            qty_item.setForeground(QColor("red"))
        self.smart_table.setItem(row, 5, qty_item)

        # ستون 6: واحد
        self.smart_table.setItem(row, 6, QTableWidgetItem(str(item.unit or '')))

        # ستون 7: امتیاز
        score_item = QTableWidgetItem(f"{score:.0f}%")
        if score >= 80:
            score_item.setForeground(QColor("green"))
        elif score >= 60:
            score_item.setForeground(QColor("orange"))
        else:
            score_item.setForeground(QColor("red"))
        self.smart_table.setItem(row, 7, score_item)

        # ستون 8: نوع تطبیق
        self.smart_table.setItem(row, 8, QTableWidgetItem(match_type))

        # ذخیره آبجکت
        self.smart_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, item)

    def search_inventory(self):
        """جستجو در موجودی انبار"""
        try:
            # نمایش وضعیت جستجو
            if hasattr(self, 'status_label'):
                self.status_label.setText("🔍 در حال جستجو...")

            # دریافت فیلترها
            search_text = self.search_input.text().strip() if hasattr(self, 'search_input') else ""
            warehouse_code = self.warehouse_combo.currentData() if hasattr(self, 'warehouse_combo') else None

            # ساخت فیلترها
            filters = {}
            if search_text:
                filters['search_text'] = search_text
            if warehouse_code:
                filters['warehouse_code'] = warehouse_code

            # جستجو در انبار
            items = self.warehouse_service.get_inventory_items(filters)

            # نمایش در جدول inventory
            self.inventory_table.setRowCount(len(items))

            for row, item in enumerate(items):
                # پر کردن ستون‌ها
                self.inventory_table.setItem(row, 0, QTableWidgetItem(str(item.item_code or '')))
                self.inventory_table.setItem(row, 1, QTableWidgetItem(str(item.warehouse_item_number or '')))
                self.inventory_table.setItem(row, 2, QTableWidgetItem(str(item.description or '')))
                self.inventory_table.setItem(row, 3, QTableWidgetItem(str(item.size or '')))
                self.inventory_table.setItem(row, 4, QTableWidgetItem(str(item.type or '')))

                # موجودی‌ها
                physical_qty = getattr(item, 'physical_qty', 0) or 0
                reserved_qty = getattr(item, 'reserved_qty', 0) or 0
                available_qty = getattr(item, 'available_qty', 0) or 0

                self.inventory_table.setItem(row, 5, QTableWidgetItem(f"{physical_qty:.2f}"))
                self.inventory_table.setItem(row, 6, QTableWidgetItem(f"{reserved_qty:.2f}"))

                qty_item = QTableWidgetItem(f"{available_qty:.2f}")
                if available_qty <= 0:
                    qty_item.setForeground(QColor("red"))
                elif available_qty < 10:
                    qty_item.setForeground(QColor("orange"))
                else:
                    qty_item.setForeground(QColor("green"))
                self.inventory_table.setItem(row, 7, qty_item)

                self.inventory_table.setItem(row, 8, QTableWidgetItem(str(item.unit or 'EA')))

                # ذخیره آبجکت در UserRole
                self.inventory_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, item)

            # به‌روزرسانی وضعیت
            if hasattr(self, 'status_label'):
                if len(items) > 0:
                    self.status_label.setText(f"✅ {len(items)} آیتم یافت شد")
                    self.status_label.setStyleSheet("""
                        QLabel {
                            color: #4CAF50;
                            font-size: 11px;
                            padding: 5px;
                            background-color: #E8F5E9;
                            border-radius: 3px;
                        }
                    """)
                else:
                    self.status_label.setText("⚠️ هیچ آیتمی یافت نشد")
                    self.status_label.setStyleSheet("""
                        QLabel {
                            color: #FF9800;
                            font-size: 11px;
                            padding: 5px;
                            background-color: #FFF3E0;
                            border-radius: 3px;
                        }
                    """)

        except Exception as e:
            logger.error(f"خطا در جستجوی انبار: {e}")
            if hasattr(self, 'status_label'):
                self.status_label.setText(f"❌ خطا: {str(e)}")
                self.status_label.setStyleSheet("""
                    QLabel {
                        color: #f44336;
                        font-size: 11px;
                        padding: 5px;
                        background-color: #FFEBEE;
                        border-radius: 3px;
                    }
                """)

    def get_selected_data(self) -> Optional[Dict]:
        """برگرداندن داده‌های آیتم انتخاب شده"""
        if not self.selected_item:
            return None

        required_qty = self.quantity_spin.value()

        # اگر selected_item یک دیکشنری است (از پیشنهادات هوشمند)
        if isinstance(self.selected_item, dict):
            return {
                'inventory_item_id': self.selected_item.get('item_id'),
                'item_code': self.selected_item.get('item_code', ''),
                'warehouse_item_number': self.selected_item.get('warehouse_item_number', ''),
                'description': self.selected_item.get('description', ''),
                'type': self.selected_item.get('type', ''),
                'size': self.selected_item.get('size', ''),
                'warehouse_code': self.selected_item.get('warehouse_code', ''),
                'warehouse_id': self.selected_item.get('warehouse_id'),
                'warehouse_name': self.selected_item.get('warehouse_name', ''),
                'mto_item_id': self.mto_item.id if self.mto_item and hasattr(self.mto_item, 'id') else None,
                'used_qty': required_qty,
                'available_qty': self.selected_item.get('available_qty', 0),
                'reserved_qty': self.selected_item.get('reserved_qty', 0),
                'unit': self.selected_item.get('unit', 'EA'),
                'confidence_score': self.selected_item.get('confidence', 1.0),
                'match_type': self.selected_item.get('match_source', 'MANUAL')
            }
        else:
            # اگر selected_item یک ORM object است
            warehouse_code = ''
            warehouse_name = ''
            warehouse_id = getattr(self.selected_item, 'warehouse_id', None)

            if warehouse_id:
                try:
                    warehouses = self.warehouse_service.get_all_warehouses()
                    for wh in warehouses:
                        if wh.id == warehouse_id:
                            warehouse_code = wh.code
                            warehouse_name = wh.name or wh.code
                            break
                except:
                    warehouse_code = f"WH{warehouse_id}"
                    warehouse_name = f"انبار {warehouse_id}"

            return {
                'inventory_item_id': self.selected_item.id if hasattr(self.selected_item, 'id') else None,
                'item_code': getattr(self.selected_item, 'item_code', ''),
                'warehouse_item_number': getattr(self.selected_item, 'warehouse_item_number', ''),
                'description': getattr(self.selected_item, 'description', ''),
                'type': getattr(self.selected_item, 'type', ''),
                'size': getattr(self.selected_item, 'size', ''),
                'warehouse_code': warehouse_code,
                'warehouse_id': warehouse_id,
                'warehouse_name': warehouse_name,
                'mto_item_id': self.mto_item.id if self.mto_item and hasattr(self.mto_item, 'id') else None,
                'used_qty': required_qty,
                'available_qty': getattr(self.selected_item, 'available_qty', 0),
                'reserved_qty': getattr(self.selected_item, 'reserved_qty', 0),
                'unit': getattr(self.selected_item, 'unit', 'EA'),
                'confidence_score': 1.0,
                'match_type': 'MANUAL'
            }

    def on_search_changed(self, text):
        """تاخیر در جستجو برای جلوگیری از جستجوی مکرر"""
        if not hasattr(self, 'search_timer'):
            self.search_timer = QTimer()
            self.search_timer.timeout.connect(self.perform_search)
            self.search_timer.setSingleShot(True)

        self.search_timer.stop()
        if len(text) >= 2:
            self.search_timer.start(500)  # 500ms تاخیر

    def accept_selection(self):
        """تأیید انتخاب آیتم و ثبت feedback"""
        try:
            if not self.selected_item:
                QMessageBox.warning(self, "خطا", "لطفاً یک آیتم انتخاب کنید")
                return

            # دریافت مقدار درخواستی
            requested_qty = self.quantity_spin.value()

            # بررسی موجودی
            available_qty = self.selected_item.get('available_qty', 0)
            if requested_qty > available_qty:
                reply = QMessageBox.question(
                    self,
                    "کسری موجودی",
                    f"موجودی فعلی ({available_qty}) کمتر از مقدار درخواستی ({requested_qty}) است.\n"
                    f"آیا می‌خواهید ادامه دهید؟",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            # ثبت feedback اگر ConsumptionService موجود باشد
            if self.consumption_service and self.mto_item:
                try:
                    # تعیین منبع انتخاب
                    match_source = self.selected_item.get('match_source', 'MANUAL')
                    confidence = self.selected_item.get('confidence', 1.0)

                    # ثبت feedback
                    feedback_result = self.consumption_service.record_user_feedback(
                        mto_code=getattr(self.mto_item, 'item_code', ''),
                        warehouse_code=self.selected_item.get('item_code', ''),
                        confidence=confidence,
                        user=self.current_user,
                        source=f"UI_{match_source}",
                        project_id=self.current_project_id
                    )

                    if feedback_result:
                        logger.info(f"Feedback recorded successfully for {self.mto_item.item_code}")

                        # ارسال سیگنال feedback
                        self.feedback_recorded.emit({
                            'mto_code': self.mto_item.item_code,
                            'warehouse_code': self.selected_item.get('item_code'),
                            'confidence': confidence,
                            'source': match_source
                        })
                    else:
                        logger.warning("Failed to record feedback")

                except Exception as e:
                    logger.error(f"Error recording feedback: {e}")
                    # ادامه حتی در صورت خطا در ثبت feedback

            # اضافه کردن اطلاعات انتخاب به selected_item
            self.selected_item['requested_quantity'] = requested_qty
            self.selected_item['selection_timestamp'] = datetime.now()
            self.selected_item['selected_by'] = self.current_user

            # ارسال سیگنال انتخاب
            self.item_selected.emit(self.selected_item)

            # نمایش پیام موفقیت
            QMessageBox.information(
                self,
                "انتخاب موفق",
                f"آیتم {self.selected_item.get('item_code')} با مقدار {requested_qty} انتخاب شد."
            )

            self.accept()

        except Exception as e:
            logger.error(f"Error in accept_selection: {e}")
            QMessageBox.critical(self, "خطا", f"خطا در ثبت انتخاب: {str(e)}")

    def _extract_row_data(self, table, row):
        """استخراج داده‌های یک ردیف از جدول"""
        data = {}
        try:
            # نام‌های ستون‌ها
            headers = []
            for col in range(table.columnCount()):
                header_item = table.horizontalHeaderItem(col)
                if header_item:
                    headers.append(header_item.text())

            # مقادیر ردیف
            for col, header in enumerate(headers):
                item = table.item(row, col)
                if item:
                    # تبدیل نام ستون فارسی به کلید انگلیسی
                    key_map = {
                        'کد آیتم': 'item_code',
                        'شماره آیتم انبار': 'warehouse_item_number',
                        'شرح': 'description',
                        'سایز': 'size',
                        'نوع': 'type',
                        'موجودی': 'available_qty',
                        'موجودی فیزیکی': 'physical_qty',
                        'قابل دسترس': 'available_qty',
                        'واحد': 'unit',
                        'امتیاز تطبیق': 'confidence',
                        'نوع تطبیق': 'match_source'
                    }

                    key = key_map.get(header, header.lower().replace(' ', '_'))
                    value = item.text()

                    # تبدیل مقادیر عددی
                    if 'qty' in key or 'confidence' in key:
                        try:
                            value = float(value.replace('%', '').replace(',', ''))
                            if 'confidence' in key:
                                value = value / 100  # تبدیل درصد به کسر
                        except:
                            pass

                    data[key] = value

        except Exception as e:
            logger.error(f"Error extracting row data: {e}")

        return data