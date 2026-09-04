"""initial schema

Revision ID: 3682bc206ab1
Revises: 
Create Date: 2026-09-03 12:24:06.830006

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3682bc206ab1'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('customers',
    sa.Column('customer_id', sa.String(), nullable=False),
    sa.Column('opted_out', sa.Boolean(), nullable=False),
    sa.Column('opted_out_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('customer_id')
    )
    op.create_table('merchants',
    sa.Column('merchant_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('category', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('merchant_id')
    )
    op.create_table('offers',
    sa.Column('offer_id', sa.String(), nullable=False),
    sa.Column('offer_type', sa.String(), nullable=False),
    sa.Column('discount_pct', sa.Float(), nullable=False),
    sa.Column('max_discount_inr', sa.Float(), nullable=False),
    sa.Column('approval_required', sa.Boolean(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('offer_id')
    )
    op.create_table('cases',
    sa.Column('case_id', sa.String(), nullable=False),
    sa.Column('merchant_id', sa.String(), nullable=True),
    sa.Column('customer_id', sa.String(), nullable=True),
    sa.Column('amount_inr', sa.Float(), nullable=False),
    sa.Column('payment_method', sa.String(), nullable=False),
    sa.Column('failure_reason', sa.String(), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=True),
    sa.Column('minutes_since_failure', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], ),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.merchant_id'], ),
    sa.PrimaryKeyConstraint('case_id')
    )
    op.create_table('audit_events',
    sa.Column('event_id', sa.String(), nullable=False),
    sa.Column('case_id', sa.String(), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['cases.case_id'], ),
    sa.PrimaryKeyConstraint('event_id')
    )
    op.create_index('ix_audit_case', 'audit_events', ['case_id'], unique=False)
    op.create_table('customer_action_history',
    sa.Column('record_id', sa.String(), nullable=False),
    sa.Column('customer_id', sa.String(), nullable=False),
    sa.Column('case_id', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('provider_reference', sa.String(), nullable=True),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['cases.case_id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], ),
    sa.PrimaryKeyConstraint('record_id')
    )
    op.create_index('ix_cah_case', 'customer_action_history', ['case_id'], unique=False)
    op.create_index('ix_cah_customer_action', 'customer_action_history', ['customer_id', 'action'], unique=False)
    op.create_table('recovery_decisions',
    sa.Column('decision_id', sa.String(), nullable=False),
    sa.Column('case_id', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('recovery_probability', sa.Float(), nullable=True),
    sa.Column('baseline_probability', sa.Float(), nullable=True),
    sa.Column('estimated_uplift', sa.Float(), nullable=True),
    sa.Column('expected_incremental_value_inr', sa.Float(), nullable=True),
    sa.Column('model_version', sa.String(), nullable=True),
    sa.Column('selected', sa.Boolean(), nullable=True),
    sa.Column('approved', sa.Boolean(), nullable=True),
    sa.Column('requires_human_approval', sa.Boolean(), nullable=True),
    sa.Column('reason_code', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['cases.case_id'], ),
    sa.PrimaryKeyConstraint('decision_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('recovery_decisions')
    op.drop_index('ix_cah_customer_action', table_name='customer_action_history')
    op.drop_index('ix_cah_case', table_name='customer_action_history')
    op.drop_table('customer_action_history')
    op.drop_index('ix_audit_case', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_table('cases')
    op.drop_table('offers')
    op.drop_table('merchants')
    op.drop_table('customers')
