"""add webhook events and reconciliation fields

Revision ID: 765c2f2f3035
Revises: 3682bc206ab1
Create Date: 2026-09-03 19:21:20.036100

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '765c2f2f3035'
down_revision: Union[str, Sequence[str], None] = '3682bc206ab1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('webhook_events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('razorpay_event_id', sa.String(), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('case_id', sa.String(), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('signature_valid', sa.Boolean(), nullable=False),
    sa.Column('processed', sa.Boolean(), nullable=True),
    sa.Column('processing_note', sa.Text(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('razorpay_event_id')
    )
    op.create_index('ix_webhook_event_type', 'webhook_events', ['event_type'], unique=False)
    op.add_column('cases', sa.Column('recovered_amount_inr', sa.Float(), nullable=True))
    op.add_column('cases', sa.Column('recovered_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('recovery_decisions', sa.Column('llm_diagnosis', sa.Text(), nullable=True))
    op.add_column('recovery_decisions', sa.Column('llm_evidence', sa.JSON(), nullable=True))
    op.add_column('recovery_decisions', sa.Column('llm_confidence', sa.Float(), nullable=True))
    op.add_column('recovery_decisions', sa.Column('execution_status', sa.String(), nullable=True))
    op.add_column('recovery_decisions', sa.Column('provider_reference', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('recovery_decisions', 'provider_reference')
    op.drop_column('recovery_decisions', 'execution_status')
    op.drop_column('recovery_decisions', 'llm_confidence')
    op.drop_column('recovery_decisions', 'llm_evidence')
    op.drop_column('recovery_decisions', 'llm_diagnosis')
    op.drop_column('cases', 'recovered_at')
    op.drop_column('cases', 'recovered_amount_inr')
    op.drop_index('ix_webhook_event_type', table_name='webhook_events')
    op.drop_table('webhook_events')
