"""Programmatic database migration runner for GrantLoom.

Equivalent to Liquibase runner for Python/Alembic.
Runs migrations programmatically on application startup and supports CLI invocation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.core.errors import DatabaseMigrationError

logger = logging.getLogger("grantloom.db.migrator")


def get_alembic_config(
    db_url: Optional[str] = None,
    ini_path: Optional[str | Path] = None,
) -> Config:
    """Create and configure Alembic Config object."""
    if ini_path is None:
        ini_path = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    
    ini_path = Path(ini_path)
    if not ini_path.exists():
        raise DatabaseMigrationError(f"Alembic configuration file not found at: {ini_path}")

    alembic_cfg = Config(str(ini_path))

    if db_url is None:
        settings = get_settings()
        sqlite_path = Path(settings.database.sqlite_path)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{sqlite_path.as_posix()}"

    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    return alembic_cfg


def upgrade(revision: str = "head", db_url: Optional[str] = None) -> None:
    """Run database schema upgrades up to the target revision (default: head)."""
    try:
        cfg = get_alembic_config(db_url=db_url)
        logger.info("Executing database upgrade to revision: %s", revision)
        command.upgrade(cfg, revision)
    except Exception as exc:
        raise DatabaseMigrationError(
            f"Failed to apply database upgrade to '{revision}': {exc}"
        ) from exc


def downgrade(revision: str = "-1", db_url: Optional[str] = None) -> None:
    """Run database schema downgrades to target revision (default: -1)."""
    try:
        cfg = get_alembic_config(db_url=db_url)
        logger.info("Executing database downgrade to revision: %s", revision)
        command.downgrade(cfg, revision)
    except Exception as exc:
        raise DatabaseMigrationError(
            f"Failed to apply database downgrade to '{revision}': {exc}"
        ) from exc


def get_current_revision(db_url: Optional[str] = None) -> Optional[str]:
    """Inspect database and return currently applied revision ID, or None if uninitialized."""
    cfg = get_alembic_config(db_url=db_url)
    effective_url = cfg.get_main_option("sqlalchemy.url")
    if not effective_url:
        return None

    engine = create_engine(effective_url)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("alembic_version"):
                return None
            result = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            return str(result) if result is not None else None
    except Exception as exc:
        logger.warning("Could not inspect alembic_version table: %s", exc)
        return None
    finally:
        engine.dispose()


def get_available_revisions() -> list[str]:
    """Return all discovered revision IDs from the migrations directory."""
    cfg = get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    return [rev.revision for rev in script.walk_revisions()]
