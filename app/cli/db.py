"""Database migration CLI utility for GrantLoom."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from app.db import migrator


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grantloom-db",
        description="GrantLoom Database Migration Management CLI",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # upgrade
    upgrade_parser = subparsers.add_parser("upgrade", help="Run database upgrades")
    upgrade_parser.add_argument(
        "revision",
        nargs="?",
        default="head",
        help="Target revision to upgrade to (default: head)",
    )
    upgrade_parser.add_argument("--db-url", default=None, help="Optional database URL override")

    # downgrade
    downgrade_parser = subparsers.add_parser("downgrade", help="Run database downgrades")
    downgrade_parser.add_argument(
        "revision",
        nargs="?",
        default="-1",
        help="Target revision to downgrade to (default: -1)",
    )
    downgrade_parser.add_argument("--db-url", default=None, help="Optional database URL override")

    # current
    current_parser = subparsers.add_parser("current", help="Inspect current applied revision")
    current_parser.add_argument("--db-url", default=None, help="Optional database URL override")

    # history
    subparsers.add_parser("history", help="List available migration revisions")

    args = parser.parse_args(argv)

    try:
        if args.subcommand == "upgrade":
            migrator.upgrade(revision=args.revision, db_url=args.db_url)
            print(f"Successfully upgraded database to: {args.revision}")
        elif args.subcommand == "downgrade":
            migrator.downgrade(revision=args.revision, db_url=args.db_url)
            print(f"Successfully downgraded database to: {args.revision}")
        elif args.subcommand == "current":
            rev = migrator.get_current_revision(db_url=args.db_url)
            print(f"Current database revision: {rev or 'None (unversioned)'}")
        elif args.subcommand == "history":
            revs = migrator.get_available_revisions()
            print("Available revisions:")
            for r in revs:
                print(f" - {r}")
        return 0
    except Exception as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
