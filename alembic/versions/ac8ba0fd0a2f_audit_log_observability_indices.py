"""audit log observability indices

audit_log had only an index on correlation_id (point lookups by task id).
The observability dashboard (Grafana, reading audit_log directly) aggregates
by agent and event_type over time — these composite indices make those
queries (latency per agent over time, error rate, llm_usage cost/volume)
avoid a full sequential scan as the table grows.

Revision ID: ac8ba0fd0a2f
Revises: 54d88a740b4b
Create Date: 2026-07-27 17:33:42.969448

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ac8ba0fd0a2f'
down_revision: Union[str, Sequence[str], None] = '54d88a740b4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent_created ON audit_log(agent, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_event_type_created ON audit_log(event_type, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_agent_created")
    op.execute("DROP INDEX IF EXISTS idx_audit_event_type_created")
