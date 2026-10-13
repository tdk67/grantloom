"""0001_initial_registry_and_outbox

Revision ID: 0001_initial_registry_and_outbox
Revises: 0000_baseline_init
Create Date: 2026-10-13 02:15:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_initial_registry_and_outbox'
down_revision: Union[str, None] = '0000_baseline_init'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enforce SQLite WAL mode and foreign key constraints
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA journal_mode=WAL;"))
    conn.execute(sa.text("PRAGMA foreign_keys=ON;"))

    # 1. documents table
    op.create_table(
        'documents',
        sa.Column('doc_id', sa.String(length=64), primary_key=True),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('file_type', sa.String(length=16), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('state', sa.String(length=32), nullable=False, server_default='uploaded'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_documents_content_hash', 'documents', ['content_hash'])
    op.create_index('ix_documents_state', 'documents', ['state'])
    op.create_index('ix_documents_filename', 'documents', ['filename'])

    # 2. outbox_jobs table
    op.create_table(
        'outbox_jobs',
        sa.Column('job_id', sa.String(length=64), primary_key=True),
        sa.Column('doc_id', sa.String(length=64), sa.ForeignKey('documents.doc_id', ondelete='CASCADE'), nullable=False),
        sa.Column('stage', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_outbox_jobs_status', 'outbox_jobs', ['status'])
    op.create_index('ix_outbox_jobs_doc_id', 'outbox_jobs', ['doc_id'])

    # 3. document_chunks table
    op.create_table(
        'document_chunks',
        sa.Column('chunk_id', sa.String(length=128), primary_key=True),
        sa.Column('doc_id', sa.String(length=64), sa.ForeignKey('documents.doc_id', ondelete='CASCADE'), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('sheet_name', sa.String(length=128), nullable=True),
        sa.Column('row_start', sa.Integer(), nullable=True),
        sa.Column('row_end', sa.Integer(), nullable=True),
        sa.Column('line_start', sa.Integer(), nullable=True),
        sa.Column('line_end', sa.Integer(), nullable=True),
        sa.Column('char_count', sa.Integer(), nullable=False),
        sa.Column('token_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('text_content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('metadata_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_document_chunks_doc_id', 'document_chunks', ['doc_id'])
    op.create_index('ix_document_chunks_doc_page', 'document_chunks', ['doc_id', 'page_number'])

    # 4. audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('log_id', sa.String(length=64), primary_key=True),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('doc_id', sa.String(length=64), sa.ForeignKey('documents.doc_id', ondelete='SET NULL'), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_audit_logs_event_type', 'audit_logs', ['event_type'])
    op.create_index('ix_audit_logs_doc_id', 'audit_logs', ['doc_id'])

    # 5. SQLite FTS5 table for BM25 keyword retrieval
    conn.execute(sa.text("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, doc_id UNINDEXED, text_content);"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS chunks_fts;"))
    op.drop_table('audit_logs')
    op.drop_table('document_chunks')
    op.drop_table('outbox_jobs')
    op.drop_table('documents')
