from alembic import op
import sqlalchemy as sa

revision = '0004_scope_customer_state'
down_revision = '0003_merchant_policy'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'customer_recovery_state_new',
        sa.Column('merchant_id', sa.String(80), nullable=False),
        sa.Column('customer_id', sa.String(80), nullable=False),
        sa.Column('attempt_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('contact_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('cumulative_incentive_inr', sa.Float, nullable=False, server_default='0'),
        sa.Column('last_action', sa.String(80)),
        sa.Column('last_action_at', sa.DateTime(timezone=True)),
        sa.Column('cooldown_until', sa.DateTime(timezone=True)),
        sa.Column('opted_out', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('recovered', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint('merchant_id', 'customer_id', name='pk_customer_recovery_state'),
    )
    op.execute(sa.text("""
        INSERT INTO customer_recovery_state_new
        (merchant_id, customer_id, attempt_count, contact_count, cumulative_incentive_inr,
         last_action, last_action_at, cooldown_until, opted_out, recovered)
        SELECT COALESCE(merchant_id, 'demo'), customer_id, attempt_count, contact_count,
               cumulative_incentive_inr, last_action, last_action_at, cooldown_until,
               opted_out, recovered
        FROM customer_recovery_state
    """))
    op.drop_table('customer_recovery_state')
    op.rename_table('customer_recovery_state_new', 'customer_recovery_state')
    op.create_index('ix_customer_recovery_state_merchant_id', 'customer_recovery_state', ['merchant_id'])


def downgrade():
    op.create_table(
        'customer_recovery_state_old',
        sa.Column('customer_id', sa.String(80), nullable=False),
        sa.Column('merchant_id', sa.String(80)),
        sa.Column('attempt_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('contact_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('cumulative_incentive_inr', sa.Float, nullable=False, server_default='0'),
        sa.Column('last_action', sa.String(80)),
        sa.Column('last_action_at', sa.DateTime(timezone=True)),
        sa.Column('cooldown_until', sa.DateTime(timezone=True)),
        sa.Column('opted_out', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('recovered', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint('customer_id'),
    )
    op.execute(sa.text("""
        INSERT INTO customer_recovery_state_old
        (customer_id, merchant_id, attempt_count, contact_count, cumulative_incentive_inr,
         last_action, last_action_at, cooldown_until, opted_out, recovered)
        SELECT customer_id, merchant_id, attempt_count, contact_count, cumulative_incentive_inr,
               last_action, last_action_at, cooldown_until, opted_out, recovered
        FROM customer_recovery_state
    """))
    op.drop_index('ix_customer_recovery_state_merchant_id', table_name='customer_recovery_state')
    op.drop_table('customer_recovery_state')
    op.rename_table('customer_recovery_state_old', 'customer_recovery_state')
    op.create_index('ix_customer_recovery_state_merchant_id', 'customer_recovery_state', ['merchant_id'])
