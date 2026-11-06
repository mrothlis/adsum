"""SQLAlchemy async engine and session setup."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:  # type: ignore[misc]
    async with async_session() as session:
        yield session


async def init_db() -> None:
    """Create any missing tables, and keep Alembic's bookkeeping honest.

    The schema is built by SQLAlchemy's create_all, not by migrations: the
    migration history starts from an already-existing schema and contains no
    CREATE TABLE, so `alembic upgrade head` against a truly empty database
    fails with NoSuchTableError.

    A brand-new database is therefore stamped at the current head once the
    tables exist. Without that, `alembic current` reports nothing and the
    first upgrade tries to replay history that has already been applied.
    Databases that already carry a version are left completely alone, so a
    real pending migration is never skipped.
    """
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_stamp_if_unversioned)


def _stamp_if_unversioned(connection) -> None:
    """Record the current migration head for a database that has none."""
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    context = MigrationContext.configure(connection)
    if context.get_current_revision() is not None:
        return  # existing install: leave migration state untouched

    config_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    if not config_path.exists():
        return  # running from a packaged copy without the migration tree

    from alembic.config import Config

    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head:
        context.stamp(ScriptDirectory.from_config(config), head)
