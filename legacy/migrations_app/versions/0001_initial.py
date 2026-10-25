from alembic import op
import sqlalchemy as sa
revision='0001_initial'; down_revision=None; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('recovery_cases',sa.Column('id',sa.String(80),primary_key=True),sa.Column('merchant_id',sa.String(80),index=True),sa.Column('customer_id',sa.String(80),index=True),sa.Column('amount_inr',sa.Float),sa.Column('failure_reason',sa.String(80)),sa.Column('payment_method',sa.String(40)),sa.Column('status',sa.String(30)),sa.Column('payload',sa.JSON),sa.Column('created_at',sa.DateTime(timezone=True)))
    op.create_table('approvals',sa.Column('id',sa.Integer,primary_key=True),sa.Column('case_id',sa.String(80),index=True),sa.Column('action',sa.String(80)),sa.Column('status',sa.String(20)),sa.Column('reason',sa.Text),sa.Column('decided_by',sa.String(120)),sa.Column('decided_at',sa.DateTime(timezone=True)),sa.Column('created_at',sa.DateTime(timezone=True)))
    op.create_table('idempotency_records',sa.Column('id',sa.Integer,primary_key=True),sa.Column('key',sa.String(120),unique=True,index=True),sa.Column('action',sa.String(80)),sa.Column('response',sa.JSON),sa.Column('created_at',sa.DateTime(timezone=True)))
    op.create_table('executions',sa.Column('id',sa.Integer,primary_key=True),sa.Column('case_id',sa.String(80),index=True),sa.Column('action',sa.String(80)),sa.Column('provider',sa.String(40)),sa.Column('provider_id',sa.String(120)),sa.Column('status',sa.String(30)),sa.Column('amount_inr',sa.Float),sa.Column('raw_response',sa.JSON),sa.Column('created_at',sa.DateTime(timezone=True)))
    op.create_table('webhook_events',sa.Column('id',sa.Integer,primary_key=True),sa.Column('event_id',sa.String(160),unique=True,index=True),sa.Column('event_type',sa.String(100)),sa.Column('payload',sa.JSON),sa.Column('processed',sa.Boolean),sa.Column('created_at',sa.DateTime(timezone=True)))
    op.create_table('evidence',sa.Column('id',sa.Integer,primary_key=True),sa.Column('merchant_id',sa.String(80),index=True),sa.Column('title',sa.String(200)),sa.Column('text',sa.Text),sa.Column('metadata_json',sa.JSON),sa.Column('created_at',sa.DateTime(timezone=True)))

def downgrade():
    for t in ['evidence','webhook_events','executions','idempotency_records','approvals','recovery_cases']: op.drop_table(t)
