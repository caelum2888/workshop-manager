"""Organization e organization_id nos dados de negócio (v0.2, etapa 1).

Revision ID: 20260921_01
Revises: 20260916_01

Em etapas, para preservar dados: cria `organizations`, insere a organização
inicial, adiciona a coluna anulável, preenche com a organização inicial e só
então aplica NOT NULL + FK + índice. Idempotente: em banco novo, a migration
anterior (create_all) já cria o schema final e aqui nada é refeito.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260921_01"
down_revision = "20260916_01"
branch_labels = None
depends_on = None

DEFAULT_NAME = "Demo Organization"
DEFAULT_SLUG = "demo"
TABLES = (
    "users",
    "instructors",
    "workshops",
    "class_groups",
    "students",
    "lessons",
    "monthly_reports",
)


# FKs inline (ALTER ADD COLUMN) do SQLite são refletidas sem ON DELETE e o rebuild
# em batch as recriaria sem ele; aqui são restauradas.
SET_NULL_USER_FKS = {"lessons": ("created_by_user_id", "updated_by_user_id")}
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _fk(table: str) -> str:
    return f"fk_{table}_organization_id_organizations"


def _ix(table: str) -> str:
    return f"ix_{table}_organization_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "organizations" not in inspector.get_table_names():
        op.create_table(
            "organizations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("slug", sa.String(80), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("slug", name="uq_organization_slug"),
        )

    organizations = sa.table(
        "organizations",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("active", sa.Boolean),
    )
    org_id = bind.scalar(sa.select(organizations.c.id).where(organizations.c.slug == DEFAULT_SLUG))
    if org_id is None:
        bind.execute(
            sa.insert(organizations).values(name=DEFAULT_NAME, slug=DEFAULT_SLUG, active=True)
        )
        org_id = bind.scalar(sa.select(organizations.c.id).where(organizations.c.slug == DEFAULT_SLUG))

    for table in TABLES:
        columns = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        added = "organization_id" not in columns
        if added:
            op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))
        bind.execute(
            sa.text(f"UPDATE {table} SET organization_id = :org WHERE organization_id IS NULL"),
            {"org": org_id},
        )
        if added:
            fks = {tuple(f["constrained_columns"]): f for f in sa.inspect(bind).get_foreign_keys(table)}
            with op.batch_alter_table(table, naming_convention=NAMING) as batch:
                for column in SET_NULL_USER_FKS.get(table, ()):
                    if fks.get((column,), {}).get("options", {}).get("ondelete") != "SET NULL":
                        name = f"fk_{table}_{column}_users"
                        batch.drop_constraint(name, type_="foreignkey")
                        batch.create_foreign_key(name, "users", [column], ["id"], ondelete="SET NULL")
                batch.alter_column("organization_id", existing_type=sa.Integer(), nullable=False)
                batch.create_foreign_key(_fk(table), "organizations", ["organization_id"], ["id"])
                batch.create_index(_ix(table), ["organization_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "organization_id" not in columns:
            continue
        with op.batch_alter_table(table) as batch:
            batch.drop_index(_ix(table))
            batch.drop_constraint(_fk(table), type_="foreignkey")
            batch.drop_column("organization_id")
    op.drop_table("organizations")
