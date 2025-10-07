"""add_inventory_item_id_to_mto_consumption

Revision ID: 7d5eaf7e2f73
Revises: 20251006_163250
Create Date: 2025-10-07 (تاریخ امروز)

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d5eaf7e2f73'
down_revision: Union[str, None] = '20251006_163250'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    اضافه کردن فیلد inventory_item_id به جدول mto_consumption
    برای امکان ثبت مصرف از انبار عمومی (فاز 2)
    """

    # 1. اضافه کردن ستون جدید به جدول mto_consumption
    op.add_column('mto_consumption',
        sa.Column('inventory_item_id', sa.Integer(), nullable=True)
    )

    # 2. اضافه کردن کلید خارجی به inventory_items
    op.create_foreign_key(
        'fk_mto_consumption_inventory_item',  # نام constraint
        'mto_consumption',                     # جدول source
        'inventory_items',                     # جدول target
        ['inventory_item_id'],                 # ستون source
        ['id']                                  # ستون target
    )

    # 3. اضافه کردن ایندکس برای جستجوی سریع‌تر
    op.create_index(
        'ix_mto_consumption_inventory_item',   # نام ایندکس
        'mto_consumption',                     # نام جدول
        ['inventory_item_id']                  # ستون‌ها
    )

    print("✅ فیلد inventory_item_id به جدول mto_consumption اضافه شد")
    print("✅ این فیلد برای ثبت مصرف از انبار عمومی استفاده می‌شود")


def downgrade() -> None:
    """
    حذف تغییرات در صورت نیاز به بازگشت
    """

    # 1. حذف ایندکس
    op.drop_index('ix_mto_consumption_inventory_item', table_name='mto_consumption')

    # 2. حذف کلید خارجی
    op.drop_constraint('fk_mto_consumption_inventory_item', 'mto_consumption', type_='foreignkey')

    # 3. حذف ستون
    op.drop_column('mto_consumption', 'inventory_item_id')

    print("⚠️ فیلد inventory_item_id از جدول mto_consumption حذف شد")
