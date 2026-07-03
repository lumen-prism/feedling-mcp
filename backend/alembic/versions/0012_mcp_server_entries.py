"""mcp encrypted server config storage

Revision ID: 0012_mcp_server_entries
Revises: 0011_world_book_entries
Create Date: 2026-07-03
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0012_mcp_server_entries"
down_revision = "0011_world_book_entries"
branch_labels = None
depends_on = None


_DDL = """
CREATE TABLE IF NOT EXISTS mcp_server_entries (
    user_id    TEXT NOT NULL,
    entry_id   TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    doc        JSONB NOT NULL,
    PRIMARY KEY (user_id, entry_id)
);
CREATE INDEX IF NOT EXISTS mcp_server_user_updated_idx
    ON mcp_server_entries (user_id, updated_at);
"""

_DROP = """
DROP TABLE IF EXISTS mcp_server_entries;
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute(_DROP)
