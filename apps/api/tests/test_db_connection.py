from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import build_engine
from tests.conftest import requires_db

pytestmark = requires_db


async def test_connects_and_server_version(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1
        version_num = int((await conn.execute(text("SHOW server_version_num"))).scalar_one())
    assert version_num >= 160000, "PostgreSQL 16+ required"


async def test_app_engine_session_settings(migrated_db_url: URL) -> None:
    """The application engine sets UTC, statement timeout and application_name."""
    eng = build_engine(migrated_db_url)
    try:
        async with eng.connect() as conn:
            tz = (await conn.execute(text("SHOW timezone"))).scalar()
            app = (await conn.execute(text("SHOW application_name"))).scalar()
            timeout = (await conn.execute(text("SHOW statement_timeout"))).scalar()
        assert tz == "UTC"
        assert app == "attendance-api"
        assert timeout == "30s"
    finally:
        await eng.dispose()


async def test_required_extensions_installed(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT extname FROM pg_extension"))
        names = {r[0] for r in rows}
    assert {"citext", "pg_trgm"} <= names
