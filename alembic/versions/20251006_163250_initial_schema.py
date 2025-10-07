"""Initial database schema

Revision ID: 20251006_163250
Revises: 
Create Date: 2025-10-06T16:32:50.620185

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251006_163250'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """
    Initial schema - tables already exist in database.
    This migration documents the starting point.

    Existing tables:
    - activity_logs
    - migrated_files
    - iso_file_index
    - projects
    - miv_records
    - mto_items
    - mto_progress
    - mto_consumption
    - spools
    - spool_items
    - spool_consumption
    - warehouses
    - inventory_items
    - inventory_transactions
    - material_reservations
    - inventory_adjustments
    - material_search_history
    - material_synonyms
    - spool_progress
    - item_mappings
    - warehouse_stock_snapshots
    - alembic_version
    """
    # Tables already exist, no action needed
    pass


def downgrade():
    """
    Cannot downgrade from initial state
    """
    # Would drop all tables - not recommended
    pass
