"""0002_structured_tables_and_requirements

Revision ID: 0002_structured_tables_and_requirements
Revises: 0001_initial_registry_and_outbox
Create Date: 2026-10-13 14:00:00.000000

Adds:
- structured_tables: maps every loaded spreadsheet table to its source document and sheet, so
  tabular SQL results can be traced back to citable row chunks and purged with the document.
- compliance_requirements: FR-C1 requirement records extracted from a solicitation, each linked
  to the chunk it was extracted from.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002_structured_tables_and_requirements'
down_revision: Union[str, None] = '0001_initial_registry_and_outbox'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'structured_tables',
        sa.Column('table_name', sa.String(length=255), primary_key=True),
        sa.Column('doc_id', sa.String(length=64), sa.ForeignKey('documents.doc_id', ondelete='CASCADE'), nullable=False),
        sa.Column('sheet_name', sa.String(length=128), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_structured_tables_doc_id', 'structured_tables', ['doc_id'])

    op.create_table(
        'compliance_requirements',
        sa.Column('req_id', sa.String(length=128), primary_key=True),
        sa.Column('solicitation_id', sa.String(length=64), sa.ForeignKey('documents.doc_id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_chunk_id', sa.String(length=128), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('requirement_text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('evidence_chunk_id', sa.String(length=128), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('decision_backend', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_compliance_requirements_solicitation', 'compliance_requirements', ['solicitation_id'])


def downgrade() -> None:
    op.drop_table('compliance_requirements')
    op.drop_table('structured_tables')
