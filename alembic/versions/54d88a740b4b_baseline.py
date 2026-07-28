"""baseline

Applies exactly the schema shared/db.py's runtime bootstrap (_SCHEMA +
_VECTOR_SCHEMA) creates via CREATE TABLE IF NOT EXISTS — by executing those
same strings directly (not a hand-copied second draft of the DDL), so the
table shapes have a single source of truth instead of two texts that could
silently drift apart. That runtime bootstrap is unchanged and still runs
(it's the project's best-effort, zero-config fallback); this migration is
the sanctioned path for every schema change from here on — new migrations
go in this directory, not into shared/db.py's _SCHEMA/_VECTOR_SCHEMA strings.

If your DB was already created by the runtime bootstrap before Alembic was
introduced, run `alembic stamp head` instead of `upgrade head` — the tables
already exist, this migration only needs to be recorded as applied.

Revision ID: 54d88a740b4b
Revises:
Create Date: 2026-07-27 16:43:03.891430

"""
from typing import Sequence, Union

from alembic import op
from shared.db import _SCHEMA, _VECTOR_SCHEMA

# revision identifiers, used by Alembic.
revision: str = '54d88a740b4b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(_SCHEMA)
    # Separate extension: `vector` may not be available on every Postgres
    # instance (unlike pgcrypto). Only needed by the Compliance Agent.
    op.execute(_VECTOR_SCHEMA)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS policy_chunks")
    op.execute("DROP TABLE IF EXISTS conversation_turns")
    op.execute("DROP TABLE IF EXISTS conversation_sessions")
    op.execute("DROP TABLE IF EXISTS pipeline_runs")
    op.execute("DROP TABLE IF EXISTS audit_log")
