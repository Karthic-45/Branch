#!/usr/bin/env python3
"""Apply the SQL in ``schema/`` in filename order.

    python infra/apply_schema.py --bootstrap   # once, with an admin connection
    python infra/apply_schema.py               # the migrations themselves

``000_bootstrap.sql`` is separated because it does two things the migrations
cannot: it creates the database, and it changes a cluster setting. Both must run
outside a transaction and before anything connects to ``branchable``.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from bmem.db import load_env  # noqa: E402

load_env()

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent.parent / "schema"
BOOTSTRAP = "000_bootstrap.sql"


def split_statements(sql: str) -> list[str]:
    """Split on semicolons, dropping comments and blank lines.

    Adequate because this project's schema contains no semicolons inside string
    literals or function bodies. It is a migration runner, not a SQL parser --
    if that ever stops being true, this needs to grow up.
    """
    lines = [ln.split("--")[0] for ln in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply_file(conn: psycopg.Connection, path: pathlib.Path) -> None:
    print(f"  {path.name}")
    for statement in split_statements(path.read_text()):
        first_line = statement.splitlines()[0][:72]
        try:
            with conn.cursor() as cur:
                cur.execute(statement)  # type: ignore[arg-type]
            print(f"    ok   {first_line}")
        except psycopg.errors.DuplicateTable:
            print(f"    skip {first_line}  (already exists)")
        except psycopg.Error as exc:
            print(f"    FAIL {first_line}\n         {exc}", file=sys.stderr)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="run 000_bootstrap.sql (cluster settings + CREATE DATABASE) instead "
        "of the migrations. Needs an admin connection.",
    )
    args = parser.parse_args()

    dsn = os.environ.get("BMEM_DATABASE_URL")
    if not dsn:
        print(
            "BMEM_DATABASE_URL is not set.\n"
            "  ccloud cluster sql <cluster-name> --connection-string",
            file=sys.stderr,
        )
        return 1

    if args.bootstrap:
        files = [SCHEMA_DIR / BOOTSTRAP]
        print("Applying bootstrap (cluster settings + database)")
    else:
        files = sorted(p for p in SCHEMA_DIR.glob("*.sql") if p.name != BOOTSTRAP)
        print(f"Applying {len(files)} migration(s)")

    # autocommit because CREATE DATABASE, SET CLUSTER SETTING, and CONFIGURE ZONE
    # are not permitted inside an explicit transaction.
    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in files:
            apply_file(conn, path)

    print("\nDone.")
    if not args.bootstrap:
        print("Verify with:  pytest -m cluster")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
