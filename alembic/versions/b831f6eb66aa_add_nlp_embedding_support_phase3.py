"""add_nlp_embedding_support_phase3

Revision ID: b831f6eb66aa
Revises: 6b9fc80af9e1
Create Date: [date]

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b831f6eb66aa'
down_revision = '6b9fc80af9e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # فقط اضافه کردن ستون‌های جدید NLP - حذف تمام alter_column های اضافی

    # اضافه کردن ستون‌های embedding به inventory_items
    op.add_column('inventory_items',
        sa.Column('embedding_vector', sa.JSON(), nullable=True))
    op.add_column('inventory_items',
        sa.Column('embedding_text', sa.Text(), nullable=True))
    op.add_column('inventory_items',
        sa.Column('embedding_updated_at', sa.DateTime(), nullable=True))

    # ایجاد جدول کش MTO embeddings
    op.create_table('mto_embedding_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mto_text', sa.Text(), nullable=False),
        sa.Column('mto_text_hash', sa.String(length=64), nullable=False),
        sa.Column('embedding_vector', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('hit_count', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('mto_text'),
        sa.UniqueConstraint('mto_text_hash')
    )
    op.create_index(op.f('ix_mto_embedding_cache_mto_text_hash'),
                     'mto_embedding_cache', ['mto_text_hash'], unique=False)


def downgrade() -> None:
    # حذف جدول کش
    op.drop_index(op.f('ix_mto_embedding_cache_mto_text_hash'),
                  table_name='mto_embedding_cache')
    op.drop_table('mto_embedding_cache')

    # حذف ستون‌های embedding
    op.drop_column('inventory_items', 'embedding_updated_at')
    op.drop_column('inventory_items', 'embedding_text')
    op.drop_column('inventory_items', 'embedding_vector')
