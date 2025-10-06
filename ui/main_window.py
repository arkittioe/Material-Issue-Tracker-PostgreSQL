# ui/main_window.py

from PyQt6.QtCore import *
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from config_manager import DB_HOST, DB_PORT, DB_NAME, ISO_PATH
# from ..data_manager_facade import DataManagerFacade as DataManager
from functools import partial
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from models import *
from watchdog.observers import Observer
import matplotlib.pyplot as plt
import os
import subprocess
import sys
import threading
import webbrowser
import time

# Import dialog های مورد نیاز
from .dialogs.mto_consumption_dialog import MTOConsumptionDialog
from .dialogs.spool_manager_dialog import SpoolManagerDialog
from .handlers.iso_index_handler import IsoIndexEventHandler
if __name__ != "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_manager_facade import DataManagerFacade as DataManager

class MainWindow(QMainWindow):

    def __init__(self, username, password):
        super().__init__()
        self.setWindowTitle("مدیریت MIV - نسخه 2.0")
        self.setGeometry(100, 100, 1200, 800)

        # Initialize DataManager with credentials
        self.dm = DataManager(username, password)
        self.current_project: Project | None = None
        self.current_user = username  # استفاده از username ورودی به جای os.getlogin()
        self.suggestion_data = []
        self.dashboard_password = "hossein"  # DASHBOARD_PASSWORD

        # تایمر برای Debouncing
        self.suggestion_timer = QTimer(self)
        self.suggestion_timer.setSingleShot(True)
        self.suggestion_timer.setInterval(300)  # 300 میلی‌ثانیه تاخیر

        self.iso_observer = None  # متغیر برای نگه داشتن ترد نگهبان

        # تعریف یک سیگنال در کلاس اصلی برای دریافت پیام از ترد نگهبان
        self.iso_event_handler = IsoIndexEventHandler(self.dm)

        # راه‌اندازی منوی بالای پنجره
        self.setup_menu()
        self.setup_ui()
        self.connect_signals()
        self.populate_project_combo()
        QApplication.instance().aboutToQuit.connect(self.cleanup_processes)

        self.start_iso_watcher()

    def setup_menu(self):
        """
        منوی گزارش‌ها با گزینه‌های جدید، جداکننده و منطق فعال/غیرفعال‌سازی
        """
        menu_bar = self.menuBar()
        reports_menu = menu_bar.addMenu("&Reports")

        # بخش گزارش‌های وابسته به پروژه
        self.mto_summary_action = reports_menu.addAction("MTO Summary Report")
        self.line_status_action = reports_menu.addAction("Line Status List Report")
        self.shortage_action = reports_menu.addAction("Shortage Report")

        # این اکشن‌ها در ابتدا غیرفعال هستند
        self.project_specific_actions = [self.mto_summary_action, self.line_status_action, self.shortage_action]
        for action in self.project_specific_actions:
            action.setEnabled(False)

        reports_menu.addSeparator()  # جداکننده برای زیبایی و خوانایی

        # بخش گزارش‌های سراسری (انبار)
        spool_inventory_action = reports_menu.addAction("Spool Inventory Report")
        spool_consumption_action = reports_menu.addAction("Spool Consumption History")  # گزارش جدید

        # اتصال اکشن‌ها به هندلر
        self.mto_summary_action.triggered.connect(lambda: self.handle_report_export('mto_summary'))
        self.line_status_action.triggered.connect(lambda: self.handle_report_export('line_status'))
        self.shortage_action.triggered.connect(lambda: self.handle_report_export('shortage'))
        spool_inventory_action.triggered.connect(lambda: self.handle_report_export('spool_inventory'))
        spool_consumption_action.triggered.connect(
            lambda: self.handle_report_export('spool_consumption'))  # اتصال گزارش جدید

        # منوی Help
        help_menu = menu_bar.addMenu("&Help")
        about_action = help_menu.addAction("&About")
        about_action.triggered.connect(self.show_about_dialog)

    def setup_ui(self):
        """متد اصلی برای ساخت و چیدمان تمام ویجت‌ها."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        # چیدمان اصلی به QVBoxLayout تغییر کرد تا بتوانیم لیبل را در پایین اضافه کنیم
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 5)  # تنظیم فاصله از لبه‌ها

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QFrame()
        left_layout = QVBoxLayout(left_panel)
        reg_form_frame = QFrame()
        reg_form_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.create_registration_form(reg_form_frame)
        dashboard_frame = QFrame()
        dashboard_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.create_dashboard(dashboard_frame)
        left_layout.addWidget(reg_form_frame)
        left_layout.addWidget(dashboard_frame, 1)

        right_panel = QFrame()
        right_layout = QVBoxLayout(right_panel)
        search_frame = QFrame()
        search_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.create_search_box(search_frame)
        console_frame = QFrame()
        console_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.create_console(console_frame)
        right_layout.addWidget(search_frame)
        right_layout.addWidget(console_frame, 1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([550, 650])

        # اسپلیتر به چیدمان اصلی اضافه می‌شود
        main_layout.addWidget(splitter)

        # اضافه کردن لیبل نام سازنده در پایین پنجره
        dev_label = QLabel("Developed by h.izadi")
        # استایل برای کم‌رنگ کردن و راست‌چین کردن متن
        dev_label.setStyleSheet("color: #777; padding-top: 4px;")
        dev_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        main_layout.addWidget(dev_label)

    def create_registration_form(self, parent_widget):
        # ساخت لایه‌ی اصلی فرم ثبت
        layout = QVBoxLayout(parent_widget)  # چیدمان عمودی برای فرم
        layout.addWidget(QLabel("<h2>ثبت رکورد MIV جدید</h2>"))  # عنوان فرم

        form_layout = QFormLayout()  # فرم دوبخشی لیبل/فیلد
        self.entries = {}  # دیکشنری نگهداری ویجت‌های ورودی

        # ردیف ویژه برای Line No با دکمه جستجوی فایل
        line_row_container = QWidget()  # کانتینر برای چینش افقی Line No + دکمه
        line_row = QHBoxLayout(line_row_container)  # چیدمان افقی
        line_row.setContentsMargins(0, 0, 0, 0)  # بدون حاشیه

        self.entries["Line No"] = QLineEdit()  # ورودی شماره خط
        self.entries["Line No"].setPlaceholderText(
            "شماره خط را وارد کنید (مثال: 10\"-P-210415-D6D-P).")  # راهنمای ورودی

        self.iso_search_btn = QPushButton("🔎 جستجوی فایل‌های ISO/DWG")  # دکمه جدید برای جستجو
        self.iso_search_btn.setToolTip(
            "جستجو در Y:\\Piping\\ISO بر اساس 6 رقم اولِ Line No (بدون توجه به علائم و حروف).")  # توضیح

        line_row.addWidget(self.entries["Line No"], 1)  # افزودن ورودی به ردیف
        line_row.addWidget(self.iso_search_btn)  # افزودن دکمه جستجو

        form_layout.addRow("Line No:", line_row_container)  # اضافه کردن ردیف Line No به فرم

        # بقیه فیلدها مثل قبل
        for field in ["MIV Tag", "Location", "Status", "Registered For"]:  # لیست فیلدهای دیگر
            self.entries[field] = QLineEdit()  # ایجاد ورودی
            form_layout.addRow(f"{field}:", self.entries[field])  # افزودن به فرم

        self.line_completer_model = QStringListModel()

        # Completer برای فیلد ثبت
        self.register_completer = QCompleter(self.line_completer_model, self)
        self.register_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.register_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.entries["Line No"].setCompleter(self.register_completer)
        self.register_completer.popup().setMinimumSize(240, 160)

        # اتصال دکمه جستجو به هندلر جدید
        self.iso_search_btn.clicked.connect(self.handle_iso_search)  # اتصال کلیک به تابع جستجو و نمایش نتایج

        self.register_btn = QPushButton("ثبت رکورد")  # دکمه ثبت
        layout.addLayout(form_layout)  # افزودن فرم به چیدمان
        layout.addWidget(self.register_btn)  # افزودن دکمه ثبت
        layout.addStretch()  # کشسان برای پر کردن فضا

    def create_dashboard(self, parent_widget):
        layout = QVBoxLayout(parent_widget)

        # Header
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("<h3>Line Progress Dashboard</h3>"))
        header_layout.addStretch()
        self.update_dashboard_btn = QPushButton("🔄 Update Chart")
        header_layout.addWidget(self.update_dashboard_btn)
        layout.addLayout(header_layout)

        # ⚠️ درست: اول Figure را بسازیم، بعد به آن دست بزنیم
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.fig.set_facecolor('white')  # تم روشن
        self.dashboard_ax = self.fig.add_subplot(111)
        self.dashboard_ax.set_facecolor('white')  # زمینه محور روشن

        # Canvas بعد از ساخت Figure
        self.canvas = FigureCanvas(self.fig)
        # تضمین پس‌زمینه روشن کانواس
        self.canvas.setStyleSheet("background: white;")
        layout.addWidget(self.canvas)

        # متن اولیه
        self.dashboard_ax.text(0.5, 0.5, "Enter a line number",
                               ha='center', va='center', color='black')
        self.fig.tight_layout()
        self.canvas.draw_idle()

        # نوار دکمه‌های پایین نمودار
        details_button_layout = QHBoxLayout()
        self.details_btn = QPushButton("Show Project Details")
        details_button_layout.addWidget(self.details_btn)

        self.export_line_status_btn = QPushButton("📄 Export Line Status")
        details_button_layout.addWidget(self.export_line_status_btn)
        layout.addLayout(details_button_layout)

    def create_search_box(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        layout.addWidget(QLabel("<h3>جستجو و نمایش</h3>"))

        search_layout = QHBoxLayout()
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("بخشی از شماره خط را برای جستجو و پیشنهاد وارد کنید...")
        self.search_btn = QPushButton("جستجو")

        # Completer برای فیلد جستجو
        self.search_completer = QCompleter(self.line_completer_model, self)
        self.search_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search_entry.setCompleter(self.search_completer)
        self.search_completer.popup().setMinimumSize(240, 160)  # تنظیم اندازه پاپ‌آپ
        self.search_completer.activated.connect(self.handle_completer_selection)

        search_layout.addWidget(self.search_entry)
        search_layout.addWidget(self.search_btn)
        layout.addLayout(search_layout)

    def handle_completer_selection(self, selected_text: str):
        # این تابع متن انتخاب شده را می‌گیرد
        # هر چیزی بعد از اولین فاصله را حذف می‌کند
        cleaned_text = selected_text.split(' ')[0]
        self.search_entry.setText(cleaned_text)

    def create_console(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        self.project_combo = QComboBox()
        self.load_project_btn = QPushButton("بارگذاری پروژه")

        project_layout = QHBoxLayout()
        project_layout.addWidget(QLabel("پروژه فعال:"))
        project_layout.addWidget(self.project_combo, 1)
        project_layout.addWidget(self.load_project_btn)

        layout.addLayout(project_layout)

        # لیبل برای نمایش وضعیت همگام‌سازی ISO
        self.iso_status_label = QLabel("وضعیت ایندکس ISO: در حال بررسی...")
        self.iso_status_label.setStyleSheet("padding: 4px; color: #f1fa8c;")  # رنگ زرد برای حالت اولیه

        # دکمه‌های مدیریت و آپدیت داده
        management_layout = QHBoxLayout()
        self.manage_spool_btn = QPushButton("مدیریت اسپول‌ها")
        self.update_data_btn = QPushButton("🔄 به‌روزرسانی از CSV")  # دکمه جدید
        self.update_data_btn.setStyleSheet("background-color: #e9f0ff;")

        # اضافه کردن QProgressBar برای نمایش وضعیت ایندکس
        self.iso_progress_bar = QProgressBar()
        self.iso_progress_bar.setRange(0, 100)
        self.iso_progress_bar.setValue(0)
        self.iso_progress_bar.setTextVisible(True)
        self.iso_progress_bar.setFormat("ایندکس ISO: %p%")
        self.iso_progress_bar.hide()  # در ابتدا مخفی است

        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setFont(QFont("Consolas", 11))

        self.console_output.setStyleSheet("background-color: #2b2b2b; color: #f8f8f2;")
        # self.console_output.setStyleSheet("background-color: #ffffff; color: #000000;")

        layout.addWidget(self.console_output, 1)
        layout.addLayout(management_layout)  # اضافه کردن چیدمان دکمه‌ها
        management_layout.addWidget(self.manage_spool_btn)
        management_layout.addWidget(self.update_data_btn)

        layout.addWidget(self.iso_progress_bar)

    def connect_signals(self):
        self.load_project_btn.clicked.connect(self.load_project)
        self.register_btn.clicked.connect(self.handle_registration)
        self.search_btn.clicked.connect(self.handle_search)

        self.update_dashboard_btn.clicked.connect(self.handle_update_dashboard_button_click)

        self.details_btn.clicked.connect(self.show_line_details)  # اتصال دکمه جزئیات

        # اتصال سیگنال دکمه جدید
        self.export_line_status_btn.clicked.connect(self.handle_line_status_export)

        self.entries["Line No"].textChanged.connect(self.on_text_changed)
        self.search_entry.textChanged.connect(self.on_text_changed)

        # اتصال تایمر به تابع اصلی برای گرفتن پیشنهادها
        self.suggestion_timer.timeout.connect(self.fetch_suggestions)

        # اتصال سیگنال‌ها به صورت تفکیک شده
        # ارسال виджت ورودی به عنوان آرگومان با استفاده از lambda
        register_widget = self.entries["Line No"]
        self.register_completer.activated.connect(
            lambda text: self.on_suggestion_selected(text, register_widget)
        )

        search_widget = self.search_entry
        self.search_completer.activated.connect(
            lambda text: self.on_suggestion_selected(text, search_widget)
        )

        self.manage_spool_btn.clicked.connect(self.open_spool_manager)

        self.update_data_btn.clicked.connect(self.handle_data_update_from_csv)
        self.iso_event_handler.status_updated.connect(self.update_iso_status_label)

        # اتصال سیگنال پیشرفت به اسلات جدید
        self.iso_event_handler.progress_updated.connect(self.update_iso_progress)

    def on_text_changed(self):
        """هر بار که متن تغییر می‌کند، تایمر را ری‌استارت می‌کند."""
        self.suggestion_timer.start()

    def populate_project_combo(self):
        self.project_combo.clear()
        try:
            projects = self.dm.get_all_projects()
            if not projects:
                self.project_combo.addItem("هیچ پروژه‌ای یافت نشد", userData=None)
            else:
                # یک آیتم "همه پروژه‌ها" برای حالت اولیه اضافه می‌کنیم
                self.project_combo.addItem("همه پروژه‌ها", userData=None)
                for proj in projects:
                    self.project_combo.addItem(proj.name, userData=proj)
        except Exception as e:
            self.log_to_console(f"خطا در بارگذاری پروژه‌ها: {e}", "error")

    def load_project(self):
        selected_index = self.project_combo.currentIndex()
        if selected_index == -1: return

        project_data = self.project_combo.itemData(selected_index)
        self.current_project = project_data

        # فعال/غیرفعال کردن اکشن‌های منو بر اساس انتخاب پروژه
        is_project_loaded = self.current_project is not None
        for action in self.project_specific_actions:
            action.setEnabled(is_project_loaded)

        if self.current_project:
            self.log_to_console(f"Project '{self.current_project.name}' loaded successfully.", "success")
            self.log_to_console("Project-specific reports are now enabled in the 'Reports' menu.", "info")
        else:
            self.log_to_console("Global search mode is active. Project-specific reports are disabled.", "info")

    def fetch_suggestions(self):
        """
        این متد تنها پس از اتمام زمان تایمر فراخوانی می‌شود.
        """
        # تشخیص می‌دهیم کدام فیلد ورودی فعال است
        focused_widget = QApplication.focusWidget()
        if isinstance(focused_widget, QLineEdit):
            text = focused_widget.text()
        else:
            return  # اگر هیچ فیلدی فعال نبود، کاری نکن

        if len(text) < 2:
            self.line_completer_model.setStringList([])
            return

        # 1. دریافت داده‌های کامل از دیتابیس (با کوئری بهینه)
        self.suggestion_data = self.dm.get_line_no_suggestions(text)

        # 2. استخراج متن نمایشی برای Completer
        display_list = [item['display'] for item in self.suggestion_data]
        self.line_completer_model.setStringList(display_list)

    def on_suggestion_selected(self, selected_display_text, target_widget):
        """
        وقتی کاربر یک پیشنهاد را انتخاب می‌کند، این متد فراخوانی می‌شود.
        target_widget: کادر ورودی که باید آپدیت شود.
        """
        selected_item = next((item for item in self.suggestion_data if item['display'] == selected_display_text), None)

        if not selected_item:
            return

        project_name = selected_item['project_name']
        line_no = selected_item['line_no']

        index = self.project_combo.findText(project_name, Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.project_combo.setCurrentIndex(index)
            self.load_project()

        # استفاده مستقیم از target_widget به جای focused_widget
        if target_widget:
            target_widget.blockSignals(True)
            target_widget.setText(line_no)
            target_widget.blockSignals(False)

        if self.current_project:
            self.update_line_dashboard(line_no)

    def handle_update_dashboard_button_click(self):
        """نمودار را بر اساس متن موجود در فیلد Line No به‌روز می‌کند."""
        if not self.current_project:
            self.show_message("هشدار", "لطفاً ابتدا یک پروژه را انتخاب کنید.", "warning")
            return

        line_no = self.entries["Line No"].text().strip()
        if not line_no:
            self.show_message("هشدار", "لطفاً شماره خط را برای نمایش نمودار وارد کنید.", "warning")
            return

        self.update_line_dashboard(line_no)

    def handle_registration(self):
        if not self.current_project:
            self.show_message("خطا", "لطفاً ابتدا یک پروژه را بارگذاری کنید.", "warning")
            return

        form_data = {field: widget.text().strip().upper() for field, widget in self.entries.items()}
        form_data["Registered By"] = self.current_user
        form_data["Complete"] = False  # پیش‌فرض

        if not form_data["Line No"] or not form_data["MIV Tag"]:
            self.show_message("خطا", "فیلدهای Line No و MIV Tag اجباری هستند.", "warning")
            return

        if self.dm.is_duplicate_miv_tag(form_data["MIV Tag"], self.current_project.id):
            self.show_message("خطا", f"تگ '{form_data['MIV Tag']}' در این پروژه تکراری است.", "error")
            return

        # اطمینان از وجود رکوردهای پیشرفت برای این خط
        self.dm.initialize_mto_progress_for_line(self.current_project.id, form_data["Line No"])

        dialog = MTOConsumptionDialog(self.dm, self.current_project.id, form_data["Line No"], parent=self)
        if not dialog.exec():
            self.log_to_console("ثبت رکورد لغو شد.", "warning")
            return

        consumed_items, spool_items = dialog.get_data()
        if not consumed_items and not spool_items:
            self.log_to_console("ثبت رکورد لغو شد چون هیچ آیتمی مصرف نشده بود.", "warning")
            return

        # (بهینه‌سازی شده) ساخت کامنت بدون کوئری اضافه
        comment_parts = []
        if consumed_items:
            # dialog.progress_data حاوی تمام اطلاعات مورد نیاز است
            mto_info_map = {item['mto_item_id']: item for item in dialog.progress_data}
            for item in consumed_items:
                mto_details = mto_info_map.get(item['mto_item_id'])
                if mto_details:
                    identifier = mto_details.get("Item Code") or mto_details.get(
                        "Description") or f"ID {mto_details['mto_item_id']}"
                    comment_parts.append(f"{item['used_qty']} x {identifier}")

        form_data["Comment"] = " | ".join(comment_parts)

        success, msg = self.dm.register_miv_record(self.current_project.id, form_data, consumed_items, spool_items)

        if success:
            self.log_to_console(msg, "success")
            self.update_line_dashboard()
            # پاک کردن فیلدهای فرم پس از ثبت موفق
            for field in ["MIV Tag", "Location", "Status"]:
                if field in self.entries:
                    self.entries[field].clear()
        else:
            self.log_to_console(msg, "error")

    def handle_search(self):
        """
        رکوردهای MIV را برای یک خط جستجو کرده و در یک دیالوگ نمایش می‌دهد.
        این دیالوگ شامل گزینه‌هایی برای ویرایش، حذف و گرفتن خروجی است.
        """
        if not self.current_project:
            self.show_message("خطا", "لطفاً ابتدا یک پروژه را بارگذاری کنید.", "warning")
            return

        line_no = self.search_entry.text().strip().upper()
        if not line_no:
            self.show_message("خطا", "لطفاً شماره خط برای جستجو را وارد کنید.", "warning")
            return

        # رابط کاربری را با شماره خط جدید به‌روزرسانی می‌کنیم
        self.entries["Line No"].setText(line_no)
        self.update_line_dashboard(line_no)

        # جستجو در دیتابیس
        records = self.dm.search_miv_by_line_no(self.current_project.id, line_no)

        if not records:
            self.show_message("نتیجه", f"هیچ رکوردی برای خط '{line_no}' یافت نشد.", "info")
            self.log_to_console(f"جستجو برای خط '{line_no}' نتیجه‌ای نداشت.", "warning")
            return

        self.log_to_console(f"{len(records)} رکورد برای خط '{line_no}' یافت شد.", "info")

        # ایجاد دیالوگ برای نمایش نتایج
        result_dialog = QDialog(self)
        result_dialog.setWindowTitle(f"نتایج جستجو برای خط: {line_no}")
        result_dialog.setMinimumSize(1100, 600)
        layout = QVBoxLayout(result_dialog)

        # جدول نمایش نتایج
        table = QTableWidget()
        columns = ["ID", "تگ MIV", "محل", "وضعیت", "ثبت‌کننده", "ثبت برای", "توضیحات", "تاریخ", "کامل شده"]
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setRowCount(len(records))
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)

        # پر کردن جدول
        for row, record in enumerate(records):
            table.setItem(row, 0, QTableWidgetItem(str(record.id)))
            table.setItem(row, 1, QTableWidgetItem(record.miv_tag))
            table.setItem(row, 2, QTableWidgetItem(record.location or ""))
            table.setItem(row, 3, QTableWidgetItem(record.status or ""))
            table.setItem(row, 4, QTableWidgetItem(record.registered_by or ""))
            table.setItem(row, 5, QTableWidgetItem(record.registered_for or ""))
            table.setItem(row, 6, QTableWidgetItem(record.comment or ""))
            date_str = record.last_updated.strftime("%Y-%m-%d %H:%M") if record.last_updated else ""
            table.setItem(row, 7, QTableWidgetItem(date_str))
            complete_str = "✓" if record.is_complete else "✗"

            table.setItem(row, 8, QTableWidgetItem(complete_str))

        table.resizeColumnsToContents()
        layout.addWidget(table)

        # دکمه‌های عملیات
        button_layout = QHBoxLayout()

        # دکمه ویرایش اطلاعات رکورد
        edit_btn = QPushButton("ویرایش رکورد انتخاب‌شده")
        edit_btn.clicked.connect(lambda: self.handle_edit_record(table, records, result_dialog))
        button_layout.addWidget(edit_btn)

        # 🆕 دکمه ویرایش آیتم‌های MIV
        edit_items_btn = QPushButton("ویرایش آیتم‌های رکورد")
        edit_items_btn.setStyleSheet("background-color: #e3f2fd; font-weight: bold;")
        edit_items_btn.clicked.connect(lambda: self.handle_edit_items(table, records, result_dialog))
        button_layout.addWidget(edit_items_btn)

        # دکمه حذف
        delete_btn = QPushButton("حذف رکورد انتخاب‌شده")
        delete_btn.setStyleSheet("background-color: #ff5555;")
        delete_btn.clicked.connect(lambda: self.handle_delete_record(table, records, result_dialog))
        button_layout.addWidget(delete_btn)

        # دکمه خروجی اکسل
        export_btn = QPushButton("📊 خروجی Excel")
        export_btn.clicked.connect(lambda: self.export_search_results(records, line_no))
        button_layout.addWidget(export_btn)

        button_layout.addStretch()

        # دکمه بستن
        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(result_dialog.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)
        result_dialog.exec()

    def handle_edit_record(self, table, records, parent_dialog):
        """ویرایش رکورد انتخاب‌شده"""
        current_row = table.currentRow()
        if current_row < 0:
            self.show_message("خطا", "لطفاً یک رکورد برای ویرایش انتخاب کنید.", "warning")
            return

        record_id = int(table.item(current_row, 0).text())
        selected_record = next((r for r in records if r.id == record_id), None)

        if not selected_record:
            return

        # دیالوگ ویرایش
        edit_dialog = QDialog(parent_dialog)
        edit_dialog.setWindowTitle(f"ویرایش رکورد MIV: {selected_record.miv_tag}")
        edit_dialog.setModal(True)
        edit_dialog.setMinimumWidth(500)

        layout = QVBoxLayout(edit_dialog)
        form_layout = QFormLayout()

        # فیلدهای قابل ویرایش
        fields = {
            "Location": QLineEdit(selected_record.location or ""),
            "Status": QLineEdit(selected_record.status or ""),
            "Registered For": QLineEdit(selected_record.registered_for or ""),
            "Comment": QLineEdit(selected_record.comment or ""),
            "Complete": QCheckBox()
        }

        fields["Complete"].setChecked(selected_record.is_complete)


        for label, widget in fields.items():
            form_layout.addRow(f"{label}:", widget)

        layout.addLayout(form_layout)

        # دکمه‌ها
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(edit_dialog.accept)
        buttons.rejected.connect(edit_dialog.reject)
        layout.addWidget(buttons)

        if edit_dialog.exec():
            # به‌روزرسانی رکورد
            update_data = {
                "location": fields["Location"].text().strip(),
                "status": fields["Status"].text().strip(),
                "registered_for": fields["Registered For"].text().strip(),
                "comment": fields["Comment"].text().strip(),
                "complete": fields["Complete"].isChecked()
            }

            success, msg = self.dm.update_miv_record(record_id, update_data)

            if success:
                self.log_to_console(f"رکورد {selected_record.miv_tag} با موفقیت ویرایش شد.", "success")
                # بستن دیالوگ‌ها و جستجوی مجدد
                parent_dialog.close()
                self.handle_search()
            else:
                self.show_message("خطا", f"خطا در ویرایش رکورد: {msg}", "error")

    def handle_delete_record(self, table, records, parent_dialog):
        """حذف رکورد انتخاب‌شده"""
        current_row = table.currentRow()
        if current_row < 0:
            self.show_message("خطا", "لطفاً یک رکورد برای حذف انتخاب کنید.", "warning")
            return

        record_id = int(table.item(current_row, 0).text())
        selected_record = next((r for r in records if r.id == record_id), None)

        if not selected_record:
            return

        # تأیید حذف
        reply = QMessageBox.question(
            parent_dialog,
            "تأیید حذف",
            f"آیا از حذف رکورد '{selected_record.miv_tag}' اطمینان دارید؟\n"
            f"این عمل غیرقابل بازگشت است!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            success, msg = self.dm.delete_miv_record(record_id)

            if success:
                self.log_to_console(f"رکورد {selected_record.miv_tag} با موفقیت حذف شد.", "success")
                # بستن دیالوگ و جستجوی مجدد
                parent_dialog.close()
                self.handle_search()
                # به‌روزرسانی نمودار
                self.update_line_dashboard()
            else:
                self.show_message("خطا", f"خطا در حذف رکورد: {msg}", "error")

    def handle_edit_items(self, table, records, parent_dialog):
        """ویرایش آیتم‌های یک رکورد MIV انتخاب‌شده"""
        current_row = table.currentRow()
        if current_row < 0:
            self.show_message("خطا", "لطفاً یک رکورد برای ویرایش آیتم‌ها انتخاب کنید.", "warning")
            return

        record_id = int(table.item(current_row, 0).text())
        selected_record = next((r for r in records if r.id == record_id), None)

        if not selected_record:
            return

        # باز کردن دیالوگ ویرایش آیتم‌ها
        from ui.dialogs.mto_consumption_dialog import MTOConsumptionDialog

        dialog = MTOConsumptionDialog(
            dm=self.dm,
            project_id=self.current_project.id,
            line_no=selected_record.line_no,
            miv_record_id=selected_record.id,  # حالت Edit
            parent=parent_dialog
        )

        if dialog.exec():
            consumed_data, spool_data = dialog.get_data()

            success, msg = self.dm.update_miv_items(
                miv_record_id=selected_record.id,
                updated_items=consumed_data,
                updated_spool_items=spool_data,
                user=self.current_user
            )

            if success:
                self.log_to_console(f"آیتم‌های MIV '{selected_record.miv_tag}' با موفقیت به‌روزرسانی شد.", "success")
                # بستن دیالوگ و جستجوی مجدد
                parent_dialog.close()
                self.handle_search()
                # به‌روزرسانی نمودار اگر نیاز باشه
                if selected_record.line_no == self.entries["Line No"].text().strip():
                    self.update_line_dashboard(selected_record.line_no)
            else:
                self.show_message("خطا", f"خطا در به‌روزرسانی آیتم‌ها: {msg}", "error")

    def export_search_results(self, records, line_no):
        """خروجی نتایج جستجو به فایل Excel"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"ذخیره نتایج جستجو برای خط {line_no}",
            f"search_results_{line_no}_{QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')}.xlsx",
            "Excel Files (*.xlsx)"
        )

        if not file_path:
            return

        try:
            import pandas as pd

            # تبدیل رکوردها به DataFrame
            data = []
            for record in records:
                data.append({
                    "ID": record.id,
                    "تگ MIV": record.miv_tag,
                    "شماره خط": record.line_no,
                    "محل": record.location or "",
                    "وضعیت": record.status or "",
                    "ثبت‌کننده": record.registered_by or "",
                    "ثبت برای": record.registered_for or "",
                    "توضیحات": record.comment or "",
                    "تاریخ ثبت": record.created_at.strftime("%Y-%m-%d %H:%M") if record.created_at else "",
                    "کامل شده": "بله" if record.complete else "خیر"
                })

            df = pd.DataFrame(data)
            df.to_excel(file_path, index=False, engine='openpyxl')

            self.log_to_console(f"نتایج جستجو در فایل {file_path} ذخیره شد.", "success")

            # باز کردن فایل
            if sys.platform == "win32":
                os.startfile(file_path)
            else:
                subprocess.call(["open", file_path])

        except Exception as e:
            self.show_message("خطا", f"خطا در ذخیره فایل: {str(e)}", "error")

    def handle_data_update_from_csv(self):
        """به‌روزرسانی داده‌های MTO از فایل CSV"""
        if not self.current_project:
            self.show_message("خطا", "لطفاً ابتدا یک پروژه را بارگذاری کنید.", "warning")
            return

        # انتخاب فایل CSV
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"انتخاب فایل CSV برای پروژه {self.current_project.name}",
            "",
            "CSV Files (*.csv);;All Files (*.*)"
        )

        if not file_path:
            return

        # دیالوگ تأیید
        reply = QMessageBox.question(
            self,
            "تأیید به‌روزرسانی",
            f"آیا از به‌روزرسانی داده‌های MTO پروژه '{self.current_project.name}' از فایل:\n"
            f"{os.path.basename(file_path)}\n"
            f"اطمینان دارید؟\n\n"
            f"توجه: این عملیات داده‌های موجود را به‌روزرسانی می‌کند.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # پردازش فایل با نمایش پیشرفت
        try:
            self.log_to_console(f"شروع به‌روزرسانی از فایل {os.path.basename(file_path)}...", "info")

            # خواندن و پردازش CSV
            import pandas as pd
            df = pd.read_csv(file_path)

            # بررسی ستون‌های مورد نیاز
            required_columns = ["Line No", "Item Code", "Size", "Schedule", "Description", "QTY", "Unit"]
            missing_columns = [col for col in required_columns if col not in df.columns]

            if missing_columns:
                self.show_message(
                    "خطا",
                    f"ستون‌های مورد نیاز در فایل CSV یافت نشد:\n{', '.join(missing_columns)}",
                    "error"
                )
                return

            # به‌روزرسانی داده‌ها
            success_count = 0
            error_count = 0
            total_rows = len(df)

            for index, row in df.iterrows():
                try:
                    # آماده‌سازی داده‌ها
                    mto_data = {
                        "line_no": str(row["Line No"]).strip().upper(),
                        "item_code": str(row["Item Code"]).strip() if pd.notna(row["Item Code"]) else "",
                        "size": str(row["Size"]).strip() if pd.notna(row["Size"]) else "",
                        "schedule": str(row["Schedule"]).strip() if pd.notna(row["Schedule"]) else "",
                        "description": str(row["Description"]).strip() if pd.notna(row["Description"]) else "",
                        "qty": float(row["QTY"]) if pd.notna(row["QTY"]) else 0,
                        "unit": str(row["Unit"]).strip() if pd.notna(row["Unit"]) else ""
                    }

                    # ثبت یا به‌روزرسانی در دیتابیس
                    if self.dm.upsert_mto_item(self.current_project.id, mto_data):
                        success_count += 1
                    else:
                        error_count += 1

                    # نمایش پیشرفت
                    if (index + 1) % 50 == 0:
                        progress = int((index + 1) / total_rows * 100)
                        self.log_to_console(f"پیشرفت: {progress}% ({index + 1}/{total_rows})", "info")

                except Exception as e:
                    error_count += 1
                    self.log_to_console(f"خطا در ردیف {index + 1}: {str(e)}", "error")

            # نمایش نتیجه نهایی
            self.log_to_console(
                f"به‌روزرسانی کامل شد!\n"
                f"موفق: {success_count} رکورد\n"
                f"خطا: {error_count} رکورد",
                "success" if error_count == 0 else "warning"
            )

            # نمایش پیام خلاصه
            QMessageBox.information(
                self,
                "نتیجه به‌روزرسانی",
                f"عملیات به‌روزرسانی کامل شد.\n\n"
                f"تعداد رکوردهای موفق: {success_count}\n"
                f"تعداد رکوردهای ناموفق: {error_count}"
            )

        except Exception as e:
            self.show_message("خطا", f"خطا در پردازش فایل CSV:\n{str(e)}", "error")
            self.log_to_console(f"خطا در به‌روزرسانی: {str(e)}", "error")

    def handle_iso_search(self):
        """جستجو در فایل‌های ISO/DWG بر اساس 6 رقم اول Line No"""
        line_no = self.entries["Line No"].text().strip()
        if not line_no:
            self.show_message("خطا", "لطفاً ابتدا شماره خط را وارد کنید.", "warning")
            return

        # استخراج 6 رقم (فقط اعداد)
        digits = ''.join(filter(str.isdigit, line_no))
        if len(digits) < 6:
            self.show_message("خطا", "شماره خط باید حداقل شامل 6 رقم باشد.", "warning")
            return

        search_pattern = digits[:6]
        self.log_to_console(f"جستجو برای فایل‌های مرتبط با الگوی: {search_pattern}", "info")

        # جستجو در ایندکس ISO
        results = self.dm.search_iso_index(search_pattern)

        if not results:
            self.show_message("نتیجه", f"هیچ فایلی با الگوی '{search_pattern}' یافت نشد.", "info")
            return

        # نمایش نتایج در دیالوگ
        result_dialog = QDialog(self)
        result_dialog.setWindowTitle(f"نتایج جستجو ISO/DWG - الگو: {search_pattern}")
        result_dialog.setMinimumSize(800, 500)
        layout = QVBoxLayout(result_dialog)

        # جدول نتایج
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["نام فایل", "نوع", "تاریخ تغییر", "عملیات"])
        table.setRowCount(len(results))
        table.horizontalHeader().setStretchLastSection(True)

        for row, result in enumerate(results):
            # نام فایل
            table.setItem(row, 0, QTableWidgetItem(result.file_name))

            # نوع فایل
            file_type = "PDF" if result.file_name.lower().endswith('.pdf') else "DWG"
            table.setItem(row, 1, QTableWidgetItem(file_type))

            # تاریخ تغییر
            date_str = result.last_modified.strftime("%Y-%m-%d %H:%M") if result.last_modified else ""
            table.setItem(row, 2, QTableWidgetItem(date_str))

            # دکمه باز کردن
            open_btn = QPushButton("باز کردن")
            open_btn.clicked.connect(partial(self.open_iso_file, result.file_path))
            table.setCellWidget(row, 3, open_btn)

        table.resizeColumnsToContents()
        layout.addWidget(table)

        # دکمه‌ها
        button_layout = QHBoxLayout()

        # دکمه باز کردن پوشه
        open_folder_btn = QPushButton("📁 باز کردن پوشه ISO")
        open_folder_btn.clicked.connect(lambda: os.startfile(ISO_PATH))
        button_layout.addWidget(open_folder_btn)

        button_layout.addStretch()

        close_btn = QPushButton("بستن")
        close_btn.clicked.connect(result_dialog.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        # نمایش تعداد نتایج
        layout.addWidget(QLabel(f"تعداد فایل‌های یافت شده: {len(results)}"))

        result_dialog.exec()

    def open_iso_file(self, file_path):
        """باز کردن فایل ISO/DWG"""
        try:
            if os.path.exists(file_path):
                if sys.platform == "win32":
                    os.startfile(file_path)
                else:
                    subprocess.call(["open", file_path])
                self.log_to_console(f"فایل {os.path.basename(file_path)} باز شد.", "success")
            else:
                self.show_message("خطا", f"فایل در مسیر مورد نظر یافت نشد:\n{file_path}", "error")
        except Exception as e:
            self.show_message("خطا", f"خطا در باز کردن فایل:\n{str(e)}", "error")

    def handle_report_export(self, report_type):
        """مدیریت خروجی گزارش‌های مختلف"""
        try:
            if report_type in ['mto_summary', 'line_status', 'shortage'] and not self.current_project:
                self.show_message("خطا", "لطفاً ابتدا یک پروژه را بارگذاری کنید.", "warning")
                return

            # تعیین نام فایل پیش‌فرض
            timestamp = QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')
            if report_type == 'mto_summary':
                default_name = f"MTO_Summary_{self.current_project.name}_{timestamp}.xlsx"
            elif report_type == 'line_status':
                default_name = f"Line_Status_{self.current_project.name}_{timestamp}.xlsx"
            elif report_type == 'shortage':
                default_name = f"Shortage_Report_{self.current_project.name}_{timestamp}.xlsx"
            elif report_type == 'spool_inventory':
                default_name = f"Spool_Inventory_{timestamp}.xlsx"
            elif report_type == 'spool_consumption':
                default_name = f"Spool_Consumption_History_{timestamp}.xlsx"
            else:
                default_name = f"Report_{timestamp}.xlsx"

            # انتخاب محل ذخیره
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "ذخیره گزارش",
                default_name,
                "Excel Files (*.xlsx)"
            )

            if not file_path:
                return

            # تولید گزارش
            self.log_to_console(f"در حال تولید گزارش {report_type}...", "info")

            if report_type == 'mto_summary':
                self.dm.export_mto_summary(self.current_project.id, file_path)
            elif report_type == 'line_status':
                self.dm.export_line_status_report(self.current_project.id, file_path)
            elif report_type == 'shortage':
                self.dm.export_shortage_report(self.current_project.id, file_path)
            elif report_type == 'spool_inventory':
                self.dm.export_spool_inventory(file_path)
            elif report_type == 'spool_consumption':
                self.dm.export_spool_consumption_history(file_path)

            self.log_to_console(f"گزارش با موفقیت در {file_path} ذخیره شد.", "success")

            # باز کردن فایل
            reply = QMessageBox.question(
                self,
                "گزارش آماده است",
                "گزارش با موفقیت ایجاد شد. آیا می‌خواهید فایل را باز کنید؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )

            if reply == QMessageBox.StandardButton.Yes:
                if sys.platform == "win32":
                    os.startfile(file_path)
                else:
                    subprocess.call(["open", file_path])

        except Exception as e:
            self.show_message("خطا", f"خطا در تولید گزارش:\n{str(e)}", "error")
            self.log_to_console(f"خطا در تولید گزارش: {str(e)}", "error")

    def handle_line_status_export(self):
        """خروجی گرفتن از وضعیت خط فعلی"""
        if not self.current_project:
            self.show_message("خطا", "لطفاً ابتدا یک پروژه را بارگذاری کنید.", "warning")
            return

        line_no = self.entries["Line No"].text().strip()
        if not line_no:
            self.show_message("خطا", "لطفاً شماره خط را وارد کنید.", "warning")
            return

        # دریافت داده‌های خط
        progress_data = self.dm.get_mto_progress_for_line(self.current_project.id, line_no)
        if not progress_data:
            self.show_message("اطلاعیه", f"هیچ داده‌ای برای خط '{line_no}' یافت نشد.", "info")
            return

        # انتخاب محل ذخیره
        timestamp = QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')
        default_name = f"Line_Status_{line_no}_{timestamp}.xlsx"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"ذخیره وضعیت خط {line_no}",
            default_name,
            "Excel Files (*.xlsx)"
        )

        if not file_path:
            return

        try:
            import pandas as pd

            # آماده‌سازی داده‌ها برای Excel
            data = []
            for item in progress_data:
                data.append({
                    "Line No": item['line_no'],
                    "Item Code": item.get('item_code', ''),
                    "Size": item.get('size', ''),
                    "Schedule": item.get('schedule', ''),
                    "Description": item.get('description', ''),
                    "Unit": item.get('unit', ''),
                    "Total Qty": item['mto_qty'],
                    "Consumed Qty": item['consumed_qty'],
                    "Remaining Qty": item['remaining_qty'],
                    "Progress %": round((item['consumed_qty'] / item['mto_qty'] * 100) if item['mto_qty'] > 0 else 0, 2)
                })

            # ایجاد DataFrame و ذخیره
            df = pd.DataFrame(data)

            # ایجاد ExcelWriter برای فرمت‌دهی بهتر
            with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Line Status', index=False)

                # دسترسی به worksheet
                worksheet = writer.sheets['Line Status']

                # تنظیم عرض ستون‌ها
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = (max_length + 2) * 1.2
                    worksheet.column_dimensions[column_letter].width = adjusted_width

            self.log_to_console(f"وضعیت خط {line_no} در فایل {file_path} ذخیره شد.", "success")

            # پیشنهاد باز کردن فایل
            reply = QMessageBox.question(
                self,
                "خروجی آماده است",
                "فایل با موفقیت ایجاد شد. آیا می‌خواهید آن را باز کنید؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )

            if reply == QMessageBox.StandardButton.Yes:
                if sys.platform == "win32":
                    os.startfile(file_path)
                else:
                    subprocess.call(["open", file_path])

        except Exception as e:
            self.show_message("خطا", f"خطا در ذخیره فایل:\n{str(e)}", "error")

    def update_line_dashboard(self, line_no=None):
        """به‌روزرسانی نمودار پیشرفت خط"""
        if not self.current_project:
            return

        if not line_no:
            line_no = self.entries["Line No"].text().strip()

        if not line_no:
            self.dashboard_ax.clear()
            self.dashboard_ax.text(0.5, 0.5, "Enter a line number", ha='center', va='center')
            self.canvas.draw()
            return

        # دریافت داده‌های پیشرفت
        progress_data = self.dm.get_mto_progress_for_line(self.current_project.id, line_no)

        if not progress_data:
            self.dashboard_ax.clear()
            self.dashboard_ax.text(0.5, 0.5, f"No data for line: {line_no}", ha='center', va='center')
            self.canvas.draw()
            return

        # محاسبه مجموع‌ها
        total_mto = sum(item['mto_qty'] for item in progress_data)
        total_consumed = sum(item['consumed_qty'] for item in progress_data)
        total_remaining = total_mto - total_consumed

        if total_mto == 0:
            self.dashboard_ax.clear()
            self.dashboard_ax.text(0.5, 0.5, f"No MTO data for line: {line_no}", ha='center', va='center')
            self.canvas.draw()
            return

        # رسم نمودار
        self.dashboard_ax.clear()

        # داده‌های پای چارت
        sizes = [total_consumed, total_remaining]
        labels = [f'Consumed\n{total_consumed:.1f}', f'Remaining\n{total_remaining:.1f}']
        colors = ['#50fa7b', '#ff5555']
        explode = (0.05, 0)

        # رسم پای چارت
        wedges, texts, autotexts = self.dashboard_ax.pie(
            sizes,
            labels=labels,
            colors=colors,
            autopct='%1.1f%%',
            startangle=90,
            explode=explode,
            shadow=True,
            textprops={'fontsize': 10}
        )

        # عنوان نمودار
        progress_percent = (total_consumed / total_mto) * 100
        self.dashboard_ax.set_title(
            f'Line {line_no} Progress: {progress_percent:.1f}%\n'
            f'Total MTO: {total_mto:.1f} | Consumed: {total_consumed:.1f}',
            fontsize=12,
            fontweight='bold',
            pad=20
        )

        self.canvas.draw()

        # به‌روزرسانی لاگ
        self.log_to_console(
            f"Dashboard updated for line {line_no}: "
            f"{progress_percent:.1f}% complete ({total_consumed:.1f}/{total_mto:.1f})",
            "info"
        )

    def log_to_console(self, message, level="info"):
        """نمایش پیام در کنسول با رنگ‌بندی"""
        timestamp = QDateTime.currentDateTime().toString("hh:mm:ss")

        # تعیین رنگ بر اساس سطح
        color_map = {
            "info": "#50fa7b",  # سبز روشن
            "warning": "#f1fa8c",  # زرد
            "error": "#ff5555",  # قرمز
            "success": "#8be9fd",  # آبی روشن
            "debug": "#6272a4"  # بنفش کم‌رنگ
        }

        color = color_map.get(level, "#f8f8f2")  # پیش‌فرض: سفید

        # فرمت HTML برای پیام
        formatted_message = (
            f'<span style="color: #6272a4;">[{timestamp}]</span> '
            f'<span style="color: {color};">{message}</span>'
        )

        self.console_output.append(formatted_message)

        # اسکرول به انتهای متن
        scrollbar = self.console_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def show_message(self, title, message, msg_type="info"):
        """نمایش پیام به کاربر"""
        if msg_type == "info":
            QMessageBox.information(self, title, message)
        elif msg_type == "warning":
            QMessageBox.warning(self, title, message)
        elif msg_type == "error":
            QMessageBox.critical(self, title, message)
        elif msg_type == "question":
            return QMessageBox.question(
                self, title, message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

    def show_line_details(self):
        """نمایش جزئیات پروژه در مرورگر (داشبورد وب)"""
        if not self.current_project:
            self.show_message("خطا", "لطفاً ابتدا یک پروژه را بارگذاری کنید.", "warning")
            return

        # دیالوگ درخواست رمز عبور
        password, ok = QInputDialog.getText(
            self,
            "احراز هویت",
            "لطفاً رمز عبور داشبورد را وارد کنید:",
            QLineEdit.EchoMode.Password
        )

        if not ok or password != self.dashboard_password:
            self.show_message("خطا", "رمز عبور نادرست است.", "error")
            return

        # تولید URL داشبورد
        dashboard_url = f"http://localhost:5000/dashboard/{self.current_project.id}"

        # بررسی وضعیت داشبورد
        if not hasattr(self, 'dashboard_process') or self.dashboard_process.poll() is not None:
            # راه‌اندازی داشبورد
            self.log_to_console("در حال راه‌اندازی داشبورد وب...", "info")

            try:
                # مسیر اسکریپت داشبورد
                dashboard_script = os.path.join(os.path.dirname(__file__), "dashboard_app.py")

                if not os.path.exists(dashboard_script):
                    self.show_message("خطا", "فایل dashboard_app.py یافت نشد.", "error")
                    return

                # اجرای داشبورد در پس‌زمینه
                self.dashboard_process = subprocess.Popen(
                    [sys.executable, dashboard_script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

                # صبر برای راه‌اندازی سرور
                time.sleep(2)

                self.log_to_console("داشبورد وب راه‌اندازی شد.", "success")

            except Exception as e:
                self.show_message("خطا", f"خطا در راه‌اندازی داشبورد:\n{str(e)}", "error")
                return

        # باز کردن مرورگر
        webbrowser.open(dashboard_url)
        self.log_to_console(f"داشبورد پروژه در مرورگر باز شد: {dashboard_url}", "info")

    def open_spool_manager(self):
        """باز کردن دیالوگ مدیریت اسپول‌ها"""
        dialog = SpoolManagerDialog(self.dm, parent=self)
        dialog.exec()

    def show_about_dialog(self):
        """نمایش دیالوگ درباره برنامه"""
        about_text = """
        <h2>سیستم مدیریت MIV</h2>
        <p><b>نسخه:</b> 2.0.0</p>
        <p><b>توسعه‌دهنده:</b> حسین ایزدی</p>
        <p><b>تاریخ:</b> 1403</p>
        <br>
        <p>این نرم‌افزار برای مدیریت و پیگیری مصرف متریال در پروژه‌های صنعتی طراحی شده است.</p>
        <br>
        <p><b>ویژگی‌های کلیدی:</b></p>
        <ul>
            <li>ثبت و پیگیری رکوردهای MIV</li>
            <li>مدیریت اسپول‌ها و مصرف از انبار</li>
            <li>نمودارهای پیشرفت خطوط</li>
            <li>گزارش‌های متنوع</li>
            <li>جستجوی پیشرفته در فایل‌های ISO</li>
        </ul>
        """

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("درباره برنامه")
        msg_box.setTextFormat(Qt.TextFormat.RichText)
        msg_box.setText(about_text)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.exec()

    def cleanup_processes(self):
        """پاکسازی فرآیندهای پس‌زمینه هنگام بستن برنامه"""
        # توقف ناظر ISO
        if self.iso_observer and self.iso_observer.is_alive():
            self.iso_observer.stop()
            self.iso_observer.join(timeout=2)

        # بستن داشبورد وب
        if hasattr(self, 'dashboard_process') and self.dashboard_process.poll() is None:
            self.dashboard_process.terminate()
            self.dashboard_process.wait()

        # بستن API
        if hasattr(self, 'api_process') and self.api_process.poll() is None:
            self.api_process.terminate()
            self.api_process.wait()

    def update_iso_status_label(self, message):
        """به‌روزرسانی لیبل وضعیت ISO"""
        self.iso_status_label.setText(message)

        # تغییر رنگ بر اساس وضعیت
        if "موفقیت" in message or "کامل" in message:
            self.iso_status_label.setStyleSheet("padding: 4px; color: #50fa7b;")  # سبز
        elif "خطا" in message:
            self.iso_status_label.setStyleSheet("padding: 4px; color: #ff5555;")  # قرمز
        else:
            self.iso_status_label.setStyleSheet("padding: 4px; color: #333333;")

    def start_iso_watcher(self):
        """راه‌اندازی ناظر تغییرات فایل‌های ISO"""
        try:
            # بررسی وجود مسیر ISO
            if not os.path.exists(ISO_PATH):
                self.update_iso_status_label(f"مسیر ISO یافت نشد: {ISO_PATH}")
                return

            # ایجاد observer
            self.iso_observer = Observer()
            self.iso_observer.schedule(self.iso_event_handler, ISO_PATH, recursive=True)
            self.iso_observer.start()

            self.update_iso_status_label("ناظر ISO فعال شد")
            self.log_to_console(f"مانیتورینگ مسیر {ISO_PATH} آغاز شد.", "success")

            # ایندکس‌سازی اولیه در ترد جداگانه
            indexing_thread = threading.Thread(target=self._initial_iso_indexing)
            indexing_thread.daemon = True
            indexing_thread.start()

        except Exception as e:
            self.update_iso_status_label(f"خطا در راه‌اندازی ناظر: {str(e)}")
            self.log_to_console(f"خطا در راه‌اندازی ناظر ISO: {str(e)}", "error")

    def _initial_iso_indexing(self):
        """ایندکس‌سازی اولیه فایل‌های ISO در پس‌زمینه"""
        try:
            self.iso_progress_bar.show()
            self.update_iso_status_label("در حال ایندکس‌سازی فایل‌های ISO...")

            # شمارش فایل‌ها
            total_files = 0
            for root, dirs, files in os.walk(ISO_PATH):
                for file in files:
                    if file.lower().endswith(('.pdf', '.dwg')):
                        total_files += 1

            if total_files == 0:
                self.update_iso_status_label("هیچ فایل ISO/DWG یافت نشد")
                self.iso_progress_bar.hide()
                return

            # ایندکس‌سازی
            processed = 0
            for root, dirs, files in os.walk(ISO_PATH):
                for file in files:
                    if file.lower().endswith(('.pdf', '.dwg')):
                        file_path = os.path.join(root, file)
                        self.dm.upsert_iso_index_entry(file_path)
                        processed += 1

                        # به‌روزرسانی پیشرفت
                        progress = int((processed / total_files) * 100)
                        self.iso_progress_bar.setValue(progress)

            self.update_iso_status_label(f"ایندکس‌سازی کامل شد: {total_files} فایل")
            self.log_to_console(f"ایندکس‌سازی {total_files} فایل ISO/DWG کامل شد.", "success")

        except Exception as e:
            self.update_iso_status_label(f"خطا در ایندکس‌سازی: {str(e)}")
            self.log_to_console(f"خطا در ایندکس‌سازی ISO: {str(e)}", "error")
        finally:
            # مخفی کردن progress bar پس از 2 ثانیه
            QTimer.singleShot(2000, self.iso_progress_bar.hide)

    def update_iso_progress(self, current, total):
        """به‌روزرسانی نوار پیشرفت ایندکس ISO"""
        if total > 0:
            progress = int((current / total) * 100)
            self.iso_progress_bar.setValue(progress)
            self.iso_progress_bar.setFormat(f"ایندکس ISO: {current}/{total} ({progress}%)")
