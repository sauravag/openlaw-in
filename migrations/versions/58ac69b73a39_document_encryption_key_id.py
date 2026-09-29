"""document encryption key id

Plan 6 Task 3b (closing a Task 3 gap): adds `key_id` to `documents` and
`document_excerpts` -- a short, non-secret label (`openlaw.documents.crypto
.key_fingerprint`'s output) naming WHICH configured key encrypted that
row's `ciphertext`/`nonce`. Task 3 shipped AES-256-GCM with no way to tell
two keys' ciphertext apart, which meant rotating the key made every
existing upload permanently unreadable (documented on `Settings
.document_encryption_key`). This column is what lets a future rotation
choose the right key to decrypt an old row with instead of guessing.

Deliberately NOT amending `a9cc99e1489f` (Task 2's migration, already
committed) -- this is a separate revision on top of it, per this task's own
constraint against rewriting migration history.

**What existing rows get, and why.** There is no production data in either
table today. If there were, every such row would necessarily predate this
migration, and therefore predate any concept of key rotation too -- Task 3
supported exactly one key, so any pre-existing row was, by construction,
encrypted with whatever single key happens to be configured. That value is
NOT safe to compute here: this migration must produce the same schema
change no matter what `openlaw.documents.crypto.key_fingerprint`'s hash
algorithm looks like years from now, so it deliberately does not import
that (mutable) application code to derive a "real" fingerprint. Instead,
existing rows are backfilled with the literal placeholder `'unknown'`,
which:

- Satisfies the NOT NULL constraint being added, so this migration is safe
  to run against a non-empty `documents`/`document_excerpts` today or in
  the future.
- Fails closed rather than silently: `'unknown'` will never match any real
  `key_fingerprint` output (a hex string), so `openlaw.documents.crypto
  .decrypt` refuses to decrypt a placeholder-tagged row (no key in any
  keyring will ever have that id) instead of guessing which key to try.
- Names the one manual step a maintainer who inherits pre-existing rows
  must take: backfill each one's real `key_id` (from operational history
  of which key was active when it was written) before it becomes
  readable again through the normal decrypt path.

The `server_default` used to backfill existing rows is dropped again at
the end of `upgrade()` -- every NEW row must specify `key_id` explicitly
(via `encrypt`'s return value), never fall back to the placeholder by
omission. This mirrors `ciphertext`/`nonce` on both tables, which have
never had a default either.

Revision ID: 58ac69b73a39
Revises: a9cc99e1489f
Create Date: 2026-09-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58ac69b73a39'
down_revision: Union[str, Sequence[str], None] = 'a9cc99e1489f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Never a real `key_fingerprint` output (those are lowercase hex) -- see
#: the module docstring for why this exact, deliberately-invalid value is
#: the correct backfill for any row that predates this migration.
_UNKNOWN_KEY_PLACEHOLDER = 'unknown'


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'documents',
        sa.Column(
            'key_id', sa.String(length=16),
            server_default=_UNKNOWN_KEY_PLACEHOLDER, nullable=False,
        ),
    )
    op.add_column(
        'document_excerpts',
        sa.Column(
            'key_id', sa.String(length=16),
            server_default=_UNKNOWN_KEY_PLACEHOLDER, nullable=False,
        ),
    )
    # Drop the server default now that any pre-existing rows are backfilled:
    # every row inserted from here on must name its own key explicitly, the
    # same way `ciphertext`/`nonce` already require an explicit value.
    op.alter_column('documents', 'key_id', server_default=None)
    op.alter_column('document_excerpts', 'key_id', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('document_excerpts', 'key_id')
    op.drop_column('documents', 'key_id')
