"""SQLAlchemy async engine and session setup."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

_IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    # A generous timeout at the driver level as well as the PRAGMA below, so a
    # brief write lock waits instead of raising. Default was 5 seconds, and a
    # lock held slightly longer than that turned a student's check-in into an
    # HTTP 500 with the attendance never recorded.
    connect_args={"timeout": 30} if _IS_SQLITE else {},
)

if _IS_SQLITE:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        """Put SQLite into a configuration fit for a classroom.

        The defaults were being used unchanged, which meant:
          * journal_mode=delete, so a single writer blocked every reader. A
            check-in and the live attendance stream would contend, and any
            other process touching the file (a backup tool, a DB browser, an
            alembic run) could stall a whole class.
          * busy_timeout=5000, chosen by nobody, so a lock held longer than
            five seconds surfaced as a 500 and lost the attendance record.
          * foreign_keys=off, so the cascade rules declared in the models
            were not actually enforced and orphan rows were possible.

        WAL lets readers and the writer proceed at once, which is exactly the
        access pattern here: many short check-in writes while the instructor's
        page streams updates.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        # NORMAL is the right trade with WAL: a power cut can lose the last
        # transaction but cannot corrupt the database.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:  # type: ignore[misc]
    async with async_session() as session:
        yield session


async def db_healthy() -> bool:
    """Can we actually reach the database?

    /healthz used to answer 200 purely because the process was running, so a
    missing column or a corrupt file showed a healthy service while every
    page returned 500, and the restart loop happily kept it up.
    """
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


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
