"""wipe legacy PLAINTEXT perception data (v1 envelope encryption cutover)

Revision ID: 0004_perception_encrypt_wipe
Revises: 0003_drop_perception_permissions
Create Date: 2026-06-12

Perception values are now encrypted at rest (v1 envelopes, like chat/memory/
identity): the report path rejects plaintext sensitive signals with 400, and
state cells hold {"env", ts} instead of {"v", ts}. Existing rows are plaintext
and unreadable by the new read path, so they are wiped rather than migrated
(prod user count is intentionally tiny; perception state has short TTLs and
repopulates from the device within minutes):

  - perception_state / perception_config blobs: state cells held cleartext
    labels (place_label, now_playing, ...); the config held home/work geofence
    COORDINATES — the most sensitive plaintext in the DB. Resolution moved
    on-device; the config is no longer stored server-side at all.
  - perception_items: workout/sleep/vitals docs were cleartext; photo rows are
    deleted too because their metadata embedded a cleartext place_label.
    Photo PIXEL envelopes (frame channel) were always ciphertext and are left
    in place — the orphaned ones are unreachable (no metadata row) and harmless.
    The iOS photo pipeline cursor only moves forward, so old photos are not
    re-uploaded; that loss is accepted.
  - perception_events / app_usage logs: wake events embedded cleartext old/new
    values; app_usage rows held cleartext app names.

Idempotent: DELETEs on already-clean sets are no-ops. Irreversible by design —
the point is that the plaintext stops existing.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_perception_encrypt_wipe"
down_revision = "0003_drop_perception_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM user_blobs WHERE kind IN ('perception_state', 'perception_config');")
    op.execute("DELETE FROM perception_items;")
    op.execute(
        "DELETE FROM user_logs WHERE stream IN ('perception_events', 'app_usage');")


def downgrade() -> None:
    # No-op: wiped plaintext is intentionally unrecoverable.
    pass
