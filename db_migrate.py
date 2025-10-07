#!/usr/bin/env python
"""ابزار مدیریت migration دیتابیس"""

import os
import sys
import locale
from pathlib import Path
from alembic import command
from alembic.config import Config
import argparse

# تنظیم encoding به UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')


def get_alembic_config():
    """پیکربندی Alembic"""
    # مسیر فایل alembic.ini
    alembic_ini_path = Path(__file__).parent / "alembic.ini"

    # ایجاد Config object
    alembic_cfg = Config(str(alembic_ini_path), encoding='utf-8')  # ⬅️ اضافه کردن encoding

    # تنظیم مسیر اسکریپت‌ها
    alembic_cfg.set_main_option("script_location", "alembic")

    return alembic_cfg


def main():
    parser = argparse.ArgumentParser(description="مدیریت migration دیتابیس")
    parser.add_argument("command", choices=["current", "history", "upgrade", "downgrade", "auto"],
                        help="دستور مورد نظر")
    parser.add_argument("--revision", help="شماره revision (برای upgrade/downgrade)")
    parser.add_argument("-m", "--message", help="پیام برای auto migration")

    args = parser.parse_args()

    # پیکربندی Alembic
    alembic_cfg = get_alembic_config()

    try:
        if args.command == "current":
            print("📍 Revision فعلی:")
            command.current(alembic_cfg, verbose=True)

        elif args.command == "history":
            print("📜 تاریخچه Migration:")
            command.history(alembic_cfg, verbose=True)

        elif args.command == "upgrade":
            revision = args.revision or "head"
            print(f"⬆️ Upgrade به {revision}...")
            command.upgrade(alembic_cfg, revision)
            print("✅ انجام شد!")

        elif args.command == "downgrade":
            if not args.revision:
                print("❌ برای downgrade باید revision مشخص کنید")
                sys.exit(1)
            print(f"⬇️ Downgrade به {args.revision}...")
            command.downgrade(alembic_cfg, args.revision)
            print("✅ انجام شد!")

        elif args.command == "auto":
            from data_manager_facade import DataManagerFacade
            print("🔍 بررسی تغییرات...")
            dm = DataManagerFacade()  # برای اطمینان از وجود جداول

            message = args.message or "Auto migration"
            command.revision(alembic_cfg, autogenerate=True, message=message)
            print(f"✅ Migration جدید ایجاد شد: {message}")

    except Exception as e:
        print(f"❌ خطا: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
