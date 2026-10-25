from alembic import op
import sqlalchemy as sa
revision='0002_customer_state'; down_revision='0001_initial'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('customer_recovery_state',sa.Column('customer_id',sa.String(80),primary_key=True),sa.Column('merchant_id',sa.String(80),index=True),sa.Column('attempt_count',sa.Integer,nullable=False,server_default='0'),sa.Column('contact_count',sa.Integer,nullable=False,server_default='0'),sa.Column('cumulative_incentive_inr',sa.Float,nullable=False,server_default='0'),sa.Column('last_action',sa.String(80)),sa.Column('last_action_at',sa.DateTime(timezone=True)),sa.Column('cooldown_until',sa.DateTime(timezone=True)),sa.Column('opted_out',sa.Boolean,nullable=False,server_default=sa.false()),sa.Column('recovered',sa.Boolean,nullable=False,server_default=sa.false()))

def downgrade():op.drop_table('customer_recovery_state')
