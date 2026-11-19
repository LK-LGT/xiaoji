"""Review and calibration center with append-only history guards.

Revision ID: 0027_review_center
Revises: 0026_performance_schemes
"""
from alembic import op
from app.domain import review_models  # noqa: F401
from app.domain.models import Base
from sqlalchemy import text

revision = "0027_review_center"
down_revision = "0026_performance_schemes"
branch_labels = None
depends_on = None

TABLES = ["review_case", "review_artifact", "review_event", "review_report", "review_score_head", "review_score_version", "review_meeting", "review_adjustment", "review_resolution", "review_outbox"]
IMMUTABLE = ["review_artifact", "review_event", "review_report", "review_score_version", "review_resolution"]


def upgrade():
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=False)
    if bind.dialect.name == "sqlite":
        for name in IMMUTABLE:
            for action in ("UPDATE", "DELETE"):
                bind.execute(text(f"CREATE TRIGGER guard_{name}_{action.lower()} BEFORE {action} ON {name} BEGIN SELECT RAISE(ABORT, 'append-only history'); END"))
    elif bind.dialect.name == "postgresql":
        bind.execute(text("CREATE FUNCTION review_history_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only history'; END; $$"))
        for name in IMMUTABLE:
            bind.execute(text(f"CREATE TRIGGER guard_{name} BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION review_history_guard()"))


def downgrade():
    bind = op.get_bind()
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind)
    if bind.dialect.name == "postgresql":
        bind.execute(text("DROP FUNCTION review_history_guard()"))
