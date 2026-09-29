"""conversations and documents

Plan 6 (conversations-and-artifacts) Task 2: gives `conversations` real
ownership (a user OR an anonymous cookie token, never neither) and
retention (`expires_at`), extends `messages` with the validated-claim
columns multi-turn answering needs, and adds the three new tables for
user-uploaded document artifacts: `documents`, `document_excerpts`, and the
`document_access_log` audit trail.

Hand-edited after `alembic revision --autogenerate` (generated against a
throwaway scratch database, never `openlaw` or `openlaw_test`) because
autogenerate does not detect a CHECK constraint that needs adding to an
EXISTING table -- `ck_conversations_has_owner` was added by hand below; the
equivalent constraint on the new `documents` table came through fine since
it is part of a `create_table`, not a diff against a live table.

Every new NOT NULL column added to an EXISTING table (`conversations.
updated_at`, and `messages.turn_index`/`claims`/`document_claims`/
`language`) carries a `server_default` so this migration does not assume the
table it is altering is empty -- see the task brief's "migrations must not
assume an empty database". The live `openlaw` database happens to be empty
today, but this migration must not depend on that.

Revision ID: a9cc99e1489f
Revises: 7039501cb932
Create Date: 2026-09-29 21:59:32.282044

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9cc99e1489f'
down_revision: Union[str, Sequence[str], None] = '7039501cb932'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'document_access_log',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('actor_kind', sa.String(length=16), nullable=False),
        sa.Column('actor_ref', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_document_access_log_created_at'),
        'document_access_log', ['created_at'], unique=False,
    )
    op.create_index(
        op.f('ix_document_access_log_document_id'),
        'document_access_log', ['document_id'], unique=False,
    )

    op.create_table(
        'documents',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('anon_token', sa.String(length=64), nullable=True),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('filename', sa.String(length=512), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=False),
        sa.Column('byte_size', sa.Integer(), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
        sa.Column('nonce', sa.LargeBinary(), nullable=False),
        sa.Column('crisis_kind', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            'user_id IS NOT NULL OR anon_token IS NOT NULL',
            name='ck_documents_has_owner',
        ),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_documents_anon_token'), 'documents', ['anon_token'], unique=False)
    op.create_index(op.f('ix_documents_conversation_id'), 'documents', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_documents_expires_at'), 'documents', ['expires_at'], unique=False)
    op.create_index(op.f('ix_documents_user_id'), 'documents', ['user_id'], unique=False)

    op.create_table(
        'document_excerpts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('excerpt_id', sa.String(length=255), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
        sa.Column('nonce', sa.LargeBinary(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'excerpt_id', name='uq_document_excerpts_document_excerpt'),
    )
    op.create_index(op.f('ix_document_excerpts_document_id'), 'document_excerpts', ['document_id'], unique=False)

    # `conversations`: ownership widened from "always a user" to "a user OR
    # an anonymous token", plus retention. `updated_at` gets a server default
    # so this ALTER does not require the table to be empty.
    op.add_column('conversations', sa.Column('anon_token', sa.String(length=64), nullable=True))
    op.add_column(
        'conversations',
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
    )
    op.add_column('conversations', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('conversations', 'user_id', existing_type=sa.VARCHAR(length=36), nullable=True)
    # Autogenerate does not detect a CHECK constraint that needs adding to an
    # already-existing table -- added by hand. Safe on non-empty data: every
    # row that predates this migration already has a non-NULL `user_id`
    # (the column was NOT NULL until the ALTER just above), so the OR check
    # holds trivially for all of them.
    op.create_check_constraint(
        'ck_conversations_has_owner',
        'conversations',
        'user_id IS NOT NULL OR anon_token IS NOT NULL',
    )
    op.create_index(op.f('ix_conversations_anon_token'), 'conversations', ['anon_token'], unique=False)
    op.create_index(op.f('ix_conversations_expires_at'), 'conversations', ['expires_at'], unique=False)

    # `messages`: validated-claim and turn-ordering columns. Each NOT NULL
    # addition carries a server default matching the ORM's Python-side
    # default, so this ALTER does not require the table to be empty.
    op.add_column(
        'messages',
        sa.Column('turn_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'messages',
        sa.Column('claims', sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.add_column(
        'messages',
        sa.Column('document_claims', sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.add_column(
        'messages',
        sa.Column('language', sa.String(length=8), server_default='en', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema.

    Reverses `conversations.user_id` back to NOT NULL last, after every
    anonymous-owned row's column has already been dropped -- if any
    anonymous conversation (`user_id IS NULL`) exists at downgrade time,
    that ALTER fails loudly rather than silently discarding ownership
    information, which is the correct failure mode for a downgrade that
    cannot represent the newer data in the older schema.
    """
    op.drop_column('messages', 'language')
    op.drop_column('messages', 'document_claims')
    op.drop_column('messages', 'claims')
    op.drop_column('messages', 'turn_index')

    op.drop_index(op.f('ix_conversations_expires_at'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_anon_token'), table_name='conversations')
    op.drop_constraint('ck_conversations_has_owner', 'conversations', type_='check')
    op.alter_column('conversations', 'user_id', existing_type=sa.VARCHAR(length=36), nullable=False)
    op.drop_column('conversations', 'expires_at')
    op.drop_column('conversations', 'updated_at')
    op.drop_column('conversations', 'anon_token')

    op.drop_index(op.f('ix_document_excerpts_document_id'), table_name='document_excerpts')
    op.drop_table('document_excerpts')

    op.drop_index(op.f('ix_documents_user_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_expires_at'), table_name='documents')
    op.drop_index(op.f('ix_documents_conversation_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_anon_token'), table_name='documents')
    op.drop_table('documents')

    op.drop_index(op.f('ix_document_access_log_document_id'), table_name='document_access_log')
    op.drop_index(op.f('ix_document_access_log_created_at'), table_name='document_access_log')
    op.drop_table('document_access_log')
