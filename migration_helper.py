#!/usr/bin/env python
"""Helper script for database migrations"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime


def run_command(cmd):
    """Execute command and show output"""
    print(f"🔧 Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(f"❌ Error: {result.stderr}")
        return False
    return True


def main():
    print("=" * 60)
    print("🗄️  Database Migration Helper")
    print("=" * 60)

    print("\nChoose an action:")
    print("1. Check current status")
    print("2. Create new migration (auto-detect changes)")
    print("3. Apply pending migrations")
    print("4. Show migration history")
    print("5. Rollback last migration")
    print("0. Exit")

    choice = input("\nEnter choice (0-5): ").strip()

    if choice == "0":
        print("Goodbye! 👋")
        sys.exit(0)

    elif choice == "1":
        print("\n📊 Current Status:")
        run_command("alembic current")
        run_command("alembic history --verbose")

    elif choice == "2":
        desc = input("\n📝 Enter migration description: ").strip()
        if not desc:
            desc = f"Update_{datetime.now().strftime('%Y%m%d_%H%M')}"

        if run_command(f'alembic revision --autogenerate -m "{desc}"'):
            print("\n✅ Migration created!")
            print("Review the file in alembic/versions/")
            apply = input("\nApply now? (y/n): ").lower()
            if apply == 'y':
                run_command("alembic upgrade head")

    elif choice == "3":
        print("\n⬆️ Applying migrations...")
        if run_command("alembic upgrade head"):
            print("✅ Database updated!")

    elif choice == "4":
        print("\n📜 Migration History:")
        run_command("alembic history")

    elif choice == "5":
        print("\n⚠️ Rolling back last migration...")
        confirm = input("Are you sure? (yes/no): ").lower()
        if confirm == "yes":
            if run_command("alembic downgrade -1"):
                print("✅ Rolled back!")
        else:
            print("Cancelled.")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
