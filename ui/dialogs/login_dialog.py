# ui/dialogs/login_dialog.py

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QDialog, QComboBox, QLineEdit, QPushButton, QCheckBox,
    QLabel, QFormLayout, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QMessageBox
)

# فرض بر این است که این متغیرها از یک فایل کانفیگ مرکزی خوانده می‌شوند
# برای تست، مقادیر پیش‌فرض قرار داده شده است
try:
    from config_manager import DB_HOST, DB_PORT, DB_NAME
except ImportError:
    DB_HOST, DB_PORT, DB_NAME = "localhost", "5432", "mydatabase"


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ورود به سیستم (PostgreSQL)")
        self.setModal(True)
        self.setMinimumWidth(380)

        # این متغیرها برای نگهداری اطلاعات پس از لاگین موفق استفاده می‌شوند
        self._username = None
        self._password = None

        self.user_combo = QComboBox()
        self.user_combo.setEditable(True)
        self.user_combo.setPlaceholderText("نام کاربری را انتخاب یا وارد کنید")

        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("رمز عبور دیتابیس")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setClearButtonEnabled(True)

        self.toggle_pass_btn = QPushButton("نمایش رمز")
        self.toggle_pass_btn.setCheckable(True)
        self.toggle_pass_btn.toggled.connect(self._toggle_password_visibility)

        self.save_pass_check = QCheckBox("ذخیره رمز عبور")

        self.server_label = QLabel(f"SERVER: {DB_HOST}:{DB_PORT} / DB: {DB_NAME}")
        self.server_label.setStyleSheet("color: gray;")

        form = QFormLayout()
        form.addRow("نام کاربری:", self.user_combo)
        form.addRow("رمز عبور:", self.pass_edit)

        buttons = QDialogButtonBox()
        self.login_btn = buttons.addButton("ورود", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_btn = buttons.addButton("انصراف", QDialogButtonBox.ButtonRole.RejectRole)

        self.login_btn.clicked.connect(self._on_login_clicked)
        self.cancel_btn.clicked.connect(self.reject)

        self.user_combo.activated.connect(self._on_user_selected)
        self.pass_edit.returnPressed.connect(self._on_login_clicked)

        wrapper = QVBoxLayout(self)
        wrapper.addWidget(self.server_label)
        wrapper.addLayout(form)
        wrapper.addWidget(self.save_pass_check)

        tools = QHBoxLayout()
        tools.addStretch(1)
        tools.addWidget(self.toggle_pass_btn)
        wrapper.addLayout(tools)

        wrapper.addWidget(buttons)

        self._load_settings()

    def _toggle_password_visibility(self, checked: bool):
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.toggle_pass_btn.setText("مخفی کردن" if checked else "نمایش رمز")

    def _on_login_clicked(self):
        username = self.user_combo.currentText().strip()
        password = self.pass_edit.text().strip()

        if not username or not password:
            QMessageBox.warning(self, "ورود نامعتبر", "نام کاربری و رمز عبور را وارد کنید.")
            return

        try:
            # Note: The data_manager needs to be in the python path
            from data_manager import DataManager
            ok, msg = DataManager.test_connection(username, password)
        except Exception as e:
            ok, msg = False, f"خطا در تست اتصال: {e}"

        if not ok:
            QMessageBox.critical(self, "اتصال ناموفق", msg)
            return

        if self.save_pass_check.isChecked():
            self._save_settings(username, password)
        else:
            # اگر کاربر نمی‌خواهد ذخیره شود، اطلاعات قبلی را پاک می‌کنیم
            self._save_settings("", "")

        # --- بخش بازسازی شده ---
        # ذخیره اطلاعات معتبر برای استفاده در خارج از کلاس
        self._username = username
        self._password = password
        self.accept()

    def _load_settings(self):
        """بارگذاری نام کاربری‌های پیش‌فرض و ذخیره‌شده از QSettings."""
        settings = QSettings("MyCompany", "MyDatabaseApp")

        # نام کاربری‌های پیش‌فرض را تعریف کنید
        default_users = ["hizadi", "raesi", "saeidi", "hassanvand"]

        # لیست نام کاربری‌های ذخیره‌شده را از QSettings بخوانید
        saved_users = settings.value("saved_users", [])

        # اگر لیست ذخیره‌شده خالی بود، لیست پیش‌فرض را جایگزین کنید
        if not saved_users:
            saved_users = default_users

        # نام کاربری‌ها را به QComboBox اضافه کنید
        self.user_combo.addItems(saved_users)

        # بازیابی آخرین نام کاربری و رمز عبور ذخیره‌شده
        saved_username = settings.value("username", "")
        saved_password = settings.value("password", "")

        if saved_username:
            # اگر نام کاربری ذخیره‌شده در لیست نیست، آن را اضافه کنید
            if saved_username not in saved_users:
                self.user_combo.addItem(saved_username)
            self.user_combo.setCurrentText(saved_username)
            self.pass_edit.setText(saved_password)
            self.save_pass_check.setChecked(True)
        else:
            self.user_combo.setCurrentIndex(-1)
            self.save_pass_check.setChecked(False)

    def _save_settings(self, username, password):
        """ذخیره نام کاربری و رمز عبور، و همچنین لیست نام کاربری‌ها در QSettings."""
        # اگر نام کاربری postgres باشد، از ذخیره آن خودداری کن
        if username.lower() == "postgres":
            return

        settings = QSettings("MyCompany", "MyDatabaseApp")
        settings.setValue("username", username)
        settings.setValue("password", password)

        # --- بخش بازسازی شده (منطق کامل) ---
        # به‌روزرسانی لیست نام کاربری‌ها
        current_users = [self.user_combo.itemText(i) for i in range(self.user_combo.count())]
        current_text = self.user_combo.currentText().strip()

        # اگر نام کاربری فعلی در لیست نیست و خالی هم نیست، آن را اضافه کنید
        if current_text and current_text not in current_users:
            current_users.append(current_text)

        settings.setValue("saved_users", current_users)

    # --- متد بازسازی شده ---
    def _on_user_selected(self):
        """وقتی کاربری از لیست کشویی انتخاب می‌شود، تنظیمات آن را بارگذاری کن."""
        selected_username = self.user_combo.currentText()
        settings = QSettings("MyCompany", "MyDatabaseApp")

        # اگر کاربر postgres باشد، هیچ چیز را بارگذاری نکن و چک‌باکس را غیرفعال کن
        if selected_username.lower() == "postgres":
            self.pass_edit.clear()
            self.save_pass_check.setChecked(False)
            self.save_pass_check.setEnabled(False)  # غیرفعال کردن چک‌باکس
            return

        # اگر کاربر دیگری باشد، چک‌باکس را فعال کن و طبق روال عمل کن
        self.save_pass_check.setEnabled(True)
        saved_username = settings.value("username", "")

        # اگر کاربر انتخاب شده همان کاربر ذخیره شده است، رمز را بارگذاری کن
        if selected_username == saved_username:
            saved_password = settings.value("password", "")
            self.pass_edit.setText(saved_password)
            self.save_pass_check.setChecked(bool(saved_password))
        else:
            self.pass_edit.clear()
            self.save_pass_check.setChecked(False)

    # --- متد بازسازی شده ---
    def get_credentials(self):
        """نام کاربری و رمز عبور معتبر را برمی‌گرداند."""
        return self._username, self._password
