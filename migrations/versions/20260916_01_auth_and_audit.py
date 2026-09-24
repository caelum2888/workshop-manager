"""Autenticação, perfis e autoria das aulas.

Revision ID: 20260916_01
Revises: None
"""

from alembic import op
import sqlalchemy as sa

from app.database import Base
from app import models  # noqa: F401

revision = "20260916_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Em banco novo, cria o schema completo. Em banco existente, create_all é
    # não destrutivo e cria apenas tabelas ausentes (como users/alembic_version).
    Base.metadata.create_all(bind=bind)
    inspector = sa.inspect(bind)
    if "lessons" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("lessons")}
        with op.batch_alter_table("lessons") as batch:
            if "created_by_user_id" not in columns:
                batch.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    "fk_lessons_created_by_user_id_users",
                    "users",
                    ["created_by_user_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if "updated_by_user_id" not in columns:
                batch.add_column(sa.Column("updated_by_user_id", sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    "fk_lessons_updated_by_user_id_users",
                    "users",
                    ["updated_by_user_id"],
                    ["id"],
                    ondelete="SET NULL",
                )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "lessons" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("lessons")}
        with op.batch_alter_table("lessons") as batch:
            if "updated_by_user_id" in columns:
                batch.drop_column("updated_by_user_id")
            if "created_by_user_id" in columns:
                batch.drop_column("created_by_user_id")
    if "users" in inspector.get_table_names():
        op.drop_table("users")

