"""stan bazy z etapu 1 i 2

Revision ID: 0001
Revises: 
Create Date: 2026-11-25 12:10:51.426832
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tabele z etapu 1 i 2 (stan bazy na serwerze 2026-11-25).
    op.create_table('api_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=True),
    sa.Column('grant_id', sa.String(length=32), nullable=False),
    sa.Column('scopes', sa.String(length=100), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('actor', sa.String(length=200), nullable=False),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=True),
    sa.Column('entity', sa.String(length=30), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('companies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('short_name', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('nip', sa.String(length=20), nullable=False),
    sa.Column('street', sa.String(length=200), nullable=False),
    sa.Column('postal_code', sa.String(length=10), nullable=False),
    sa.Column('city', sa.String(length=100), nullable=False),
    sa.Column('bank_name', sa.String(length=100), nullable=False),
    sa.Column('bank_account', sa.String(length=40), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('issue_place', sa.String(length=100), nullable=False),
    sa.Column('pattern_vat', sa.String(length=60), nullable=False),
    sa.Column('pattern_margin', sa.String(length=60), nullable=False),
    sa.Column('numbering_reset', sa.String(length=10), nullable=False),
    sa.Column('default_payment_days', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('contractors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('is_company', sa.Boolean(), nullable=False),
    sa.Column('nip', sa.String(length=20), nullable=False),
    sa.Column('pesel', sa.String(length=11), nullable=False),
    sa.Column('street', sa.String(length=200), nullable=False),
    sa.Column('postal_code', sa.String(length=10), nullable=False),
    sa.Column('city', sa.String(length=100), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('oauth_clients',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('redirect_uris', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_id')
    )
    op.create_table('oauth_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('redirect_uri', sa.Text(), nullable=False),
    sa.Column('code_challenge', sa.String(length=128), nullable=False),
    sa.Column('scopes', sa.String(length=100), nullable=False),
    sa.Column('resource', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    op.create_table('cars',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('make', sa.String(length=60), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('vin', sa.String(length=17), nullable=False),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('mileage_km', sa.Integer(), nullable=True),
    sa.Column('registration', sa.String(length=15), nullable=False),
    sa.Column('purchase_date', sa.Date(), nullable=True),
    sa.Column('purchase_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('purchase_document', sa.String(length=120), nullable=False),
    sa.Column('asking_price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('number_sequences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('last', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'kind', 'period', name='uq_sequence')
    )
    op.create_table('invoices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('number', sa.String(length=60), nullable=True),
    sa.Column('seq', sa.Integer(), nullable=True),
    sa.Column('issue_date', sa.Date(), nullable=False),
    sa.Column('sale_date', sa.Date(), nullable=False),
    sa.Column('issue_place', sa.String(length=100), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('payment_method', sa.String(length=15), nullable=False),
    sa.Column('paid', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('prices_gross', sa.Boolean(), nullable=False),
    sa.Column('contractor_id', sa.Integer(), nullable=True),
    sa.Column('buyer_name', sa.String(length=300), nullable=False),
    sa.Column('buyer_nip', sa.String(length=20), nullable=False),
    sa.Column('buyer_address', sa.Text(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('total_net', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('total_vat', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('total_gross', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('margin_total', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.ForeignKeyConstraint(['contractor_id'], ['contractors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'number', name='uq_invoice_number')
    )
    op.create_table('invoice_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('invoice_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit', sa.String(length=10), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('vat_rate', sa.String(length=3), nullable=False),
    sa.Column('net', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('vat', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('gross', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('purchase_price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('margin', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    # Tabele z etapu 1 i 2 (stan bazy na serwerze 2026-11-25).
    op.drop_table('invoice_items')
    op.drop_table('invoices')
    op.drop_table('number_sequences')
    op.drop_table('cars')
    op.drop_table('oauth_codes')
    op.drop_table('oauth_clients')
    op.drop_table('contractors')
    op.drop_table('companies')
    op.drop_table('audit_log')
    op.drop_table('api_tokens')
