from alembic import op
import sqlalchemy as sa
revision='0003_merchant_policy'; down_revision='0002_customer_state'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('merchant_policies',sa.Column('merchant_id',sa.String(80),primary_key=True),sa.Column('max_attempts',sa.Integer,nullable=False,server_default='3'),sa.Column('contact_budget',sa.Integer,nullable=False,server_default='4000'),sa.Column('incentive_budget_inr',sa.Float,nullable=False,server_default='100000'),sa.Column('max_incentive_pct',sa.Float,nullable=False,server_default='0.05'),sa.Column('approval_threshold_inr',sa.Float,nullable=False,server_default='50000'),sa.Column('cooldown_minutes',sa.Integer,nullable=False,server_default='30'),sa.Column('allowed_actions',sa.JSON,nullable=False),sa.Column('action_caps',sa.JSON,nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True)))

def downgrade():op.drop_table('merchant_policies')
