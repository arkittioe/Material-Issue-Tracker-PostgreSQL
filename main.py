#!/usr/bin/env
"""
Material Issue Tracker - Main Entry Point
========================================
"""

import sys
import os

# اضافه کردن مسیر پروژه به sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt, QCoreApplication
from PyQt6.QtGui import QIcon

# Import های محلی
from ui.utils.helpers import excepthook, resource_path, apply_light_palette
try:
    from ui.main_window import MainWindow
    from ui.dialogs.login_dialog import LoginDialog
    from ui.widgets.splash_screen import SplashScreen
    from ui.utils.helpers import excepthook, resource_path
    from data_manager import DataManager
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

def setup_application():
    """تنظیمات اولیه برنامه"""
    # تنظیمات High DPI
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    # ایجاد application
    app = QApplication(sys.argv)

    # تنظیمات برنامه
    app.setApplicationName("Material Issue Tracker")
    app.setOrganizationName("YourCompany")
    app.setOrganizationDomain("yourcompany.com")

    # استایل
    app.setStyle('Fusion')

    apply_light_palette(app)

    # آیکون برنامه (اگر وجود دارد)
    icon_path = resource_path('app_icon.ico')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Exception handler
    sys.excepthook = excepthook

    return app

def main():
    """تابع اصلی برنامه"""

    # ایجاد و تنظیم application
    app = setup_application()

    try:
        # نمایش Splash Screen
        splash = SplashScreen()
        splash.show()
        app.processEvents()

        # آماده‌سازی
        splash.showMessage("در حال آماده‌سازی...", Qt.GlobalColor.white)
        app.processEvents()

        # نمایش دیالوگ ورود
        login_dialog = LoginDialog()
        splash.close()

        if login_dialog.exec() != 1:
            sys.exit(0)

        # دریافت اطلاعات ورود
        username, password = login_dialog.get_credentials()

        if not username or not password:
            QMessageBox.critical(None, "خطا", "نام کاربری یا رمز عبور وارد نشده است!")
            sys.exit(1)

        # ایجاد و نمایش پنجره اصلی
        window = MainWindow(username, password)
        window.show()

        # اجرای event loop
        sys.exit(app.exec())

    except Exception as e:
        import traceback
        traceback.print_exc()
        QMessageBox.critical(None, "خطای بحرانی", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
