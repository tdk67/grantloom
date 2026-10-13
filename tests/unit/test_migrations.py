"""Unit tests for Alembic database migration runner and CLI."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.cli import db as db_cli
from app.db import migrator


def test_migration_upgrade_and_inspection(tmp_path: Path):
    """Assert alembic upgrade head runs cleanly on an empty SQLite DB, creates tables, and reports status."""
    test_db = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{test_db.as_posix()}"

    # Initially empty DB has no current revision
    assert migrator.get_current_revision(db_url=db_url) is None

    # Run upgrade head
    migrator.upgrade(revision="head", db_url=db_url)

    # Verify alembic_version table exists and contains the applied revision
    engine = create_engine(db_url)
    with engine.connect() as conn:
        inspector = inspect(conn)
        assert inspector.has_table("alembic_version")
        assert inspector.has_table("documents")
        assert inspector.has_table("outbox_jobs")
        assert inspector.has_table("document_chunks")
        assert inspector.has_table("audit_logs")
        
        row = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert row == "0001_initial_registry_and_outbox"
    engine.dispose()

    # Programmatic status inspection
    current_rev = migrator.get_current_revision(db_url=db_url)
    assert current_rev == "0001_initial_registry_and_outbox"

    # History lists available revisions
    available = migrator.get_available_revisions()
    assert "0001_initial_registry_and_outbox" in available
    assert "0000_baseline_init" in available


def test_migration_downgrade(tmp_path: Path):
    """Test downgrading database to baseline and base."""
    test_db = tmp_path / "test_downgrade.db"
    db_url = f"sqlite:///{test_db.as_posix()}"

    migrator.upgrade(revision="head", db_url=db_url)
    assert migrator.get_current_revision(db_url=db_url) == "0001_initial_registry_and_outbox"

    # Downgrade to baseline
    migrator.downgrade(revision="0000_baseline_init", db_url=db_url)
    assert migrator.get_current_revision(db_url=db_url) == "0000_baseline_init"

    # Downgrade to base
    migrator.downgrade(revision="base", db_url=db_url)
    assert migrator.get_current_revision(db_url=db_url) is None


def test_migration_cli_invocation(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test CLI commands: upgrade, current, history."""
    test_db = tmp_path / "test_cli.db"
    db_url = f"sqlite:///{test_db.as_posix()}"

    # CLI upgrade
    ret = db_cli.main(["upgrade", "head", "--db-url", db_url])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Successfully upgraded database to: head" in captured.out

    # CLI current
    ret = db_cli.main(["current", "--db-url", db_url])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Current database revision: 0001_initial_registry_and_outbox" in captured.out

    # CLI history
    ret = db_cli.main(["history"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "0001_initial_registry_and_outbox" in captured.out
    assert "0000_baseline_init" in captured.out
