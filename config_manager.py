# file: config_manager.py

import configparser
import os

# ایجاد نمونه ConfigParser
config = configparser.ConfigParser()

# مسیر فایل کانفیگ به صورت دینامیک
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')

# خواندن فایل کانفیگ
config.read(config_path, encoding='utf-8')

# --- استخراج مقادیر PostgreSQL ---
DB_HOST = config.get('PostgreSQL', 'host', fallback='localhost').strip()
DB_PORT = config.getint('PostgreSQL', 'port', fallback=5432)  # تبدیل به int
DB_NAME = config.get('PostgreSQL', 'dbname', fallback='miv_db').strip()
DB_USER = config.get('PostgreSQL', 'user', fallback='').strip()
DB_PASSWORD = config.get('PostgreSQL', 'password', fallback='').strip()

# --- استخراج بقیه مقادیر ---
ISO_PATH = config.get('Paths', 'iso_drawing_path', fallback=r'\\fs\Piping\Piping\ISO').strip()
DASHBOARD_PASSWORD = config.get('Security', 'dashboard_password', fallback='default_password').strip()


