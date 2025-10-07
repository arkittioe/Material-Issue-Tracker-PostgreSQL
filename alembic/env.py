# alembic/env.py
"""Alembic Environment Configuration for MIV Project"""

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os
import sys
from pathlib import Path

# ✅ اضافه کردن root پروژه به Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ✅ Import مدل‌ها و تنظیمات از پروژه
from models import Base  # این همون Base شماست که همه مدل‌ها ازش ارث‌بری می‌کنند
from config_manager import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
from urllib.parse import quote_plus

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ✅ متادیتای مدل‌های شما برای autogenerate
target_metadata = Base.metadata


def get_database_url():
    """ساخت database URL از config.ini شما"""
    # خواندن از متغیرهای محیطی یا config.ini
    user = DB_USER or os.getenv("APP_DB_USER", "postgres")
    password = DB_PASSWORD or os.getenv("APP_DB_PASSWORD", "")

    # Encode کردن username و password برای امنیت
    user_enc = quote_plus(user.strip()) if user else ""
    pwd_enc = quote_plus(password.strip()) if password else ""

    # ساخت URL کامل
    db_url = f"postgresql+psycopg2://{user_enc}:{pwd_enc}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    print(f"🔗 Connecting to database: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    return db_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    برای زمانی که می‌خواهید فقط SQL script بسازید بدون اتصال به دیتابیس
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,  # تشخیص تغییر نوع ستون‌ها
        compare_server_default=True,  # تشخیص تغییر مقدار پیش‌فرض
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    اتصال مستقیم به دیتابیس و اعمال تغییرات
    """
    # ساخت پیکربندی با URL از config.ini
    configuration = config.get_section(config.config_ini_section, {})
    configuration['sqlalchemy.url'] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # ✅ مهم: تشخیص تغییر نوع داده
            compare_server_default=True,  # ✅ مهم: تشخیص تغییر default value
            include_schemas=True,  # ✅ مهم: شامل کردن همه schema ها
        )

        with context.begin_transaction():
            context.run_migrations()


# تشخیص حالت offline یا online
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
