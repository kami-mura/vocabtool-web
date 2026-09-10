from __future__ import annotations

import argparse
import logging
import sys

from .db import SessionLocal, init_db
from .models import User

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _cmd_list_users() -> int:
    init_db()
    db = SessionLocal()
    try:
        for user in db.query(User).order_by(User.id).all():
            print(f"{user.id}\t{user.email}")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-users", help="列出用户")

    args = parser.parse_args(argv)
    _setup_logging()

    if args.command == "list-users":
        return _cmd_list_users()
    return 2


if __name__ == "__main__":
    sys.exit(main())
