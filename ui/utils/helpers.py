

# -*- coding: utf-8 -*-
"""
Helper functions for the application
"""

import sys
import os
import traceback
from PyQt6.QtWidgets import QMessageBox, QApplication
from PyQt6.QtGui import QPalette, QColor
def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def excepthook(exc_type, exc_value, exc_tb):
    """Global exception handler"""
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print("CRITICAL ERROR:", tb)
    try:
        QMessageBox.critical(None, "Critical Error", tb)
    except:
        print("Could not show error dialog")

def format_bytes(size):
    """تبدیل بایت به واحد مناسب"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def sanitize_filename(filename):
    """حذف کاراکترهای غیرمجاز از نام فایل"""
    invalid_chars = '<>:"|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '')
    return filename.strip()

def get_timestamp():
    """برگرداندن timestamp فعلی"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def apply_light_palette(app: QApplication) -> None:
    """
    اعمال پالت روشن و استاندارد برای کل برنامه
    """
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#f7f7f7"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffe1"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#f0f0f0"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ff0000"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#3874f2"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Link, QColor("#0b61a4"))
    pal.setColor(QPalette.ColorRole.LinkVisited, QColor("#5a3696"))
    app.setPalette(pal)
