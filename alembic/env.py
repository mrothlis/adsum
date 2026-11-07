"""Alembic environment — uses the app's metadata and DATABASE_URL from .env."""
from logging.config import fileConfig

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, inspect, pool

from alembic import context
from app.config import settings
from app.models import Base

config = context.config

# Sync driver for migrations (app itself runs on aiosqlite)
url = settings.DATABASE_URL.replace("+aiosqlite", "")
config.set_main_option("sqlalchemy.url", url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTER support
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Is this a brand-new database? Asked on its OWN connection, deliberately.
    #
    # Inspecting the connection that alembic is about to use leaves an
    # implicit transaction open on it, and the version update then gets rolled
    # back when the connection closes: the migration appears to run, prints
    # "Running upgrade", and the database stays on the old revision. Caught by
    # checking alembic_version before and after rather than trusting the log.
    with connectable.connect() as probe:
        database_is_empty = not inspect(probe).get_table_names()

    if database_is_empty:
        # An empty database is built, not migrated. The migration history
        # starts from an already-existing schema and contains no CREATE TABLE,
        # so running it against a fresh database died on the first revision
        # that touched a table: NoSuchTableError: courses. The install
        # documentation told people to run this command, and two separate
        # readers hit that traceback.
        #
        # So create the schema from the models and stamp it at head, which is
        # exactly what the app does on first start. A database that already
        # has tables never reaches this branch and is migrated normally.
        with connectable.begin() as conn:
            target_metadata.create_all(bind=conn)
            MigrationContext.configure(conn).stamp(
                ScriptDirectory.from_config(config), "head")
        return

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite ALTER support
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
