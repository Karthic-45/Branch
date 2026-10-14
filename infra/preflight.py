#!/usr/bin/env python3
"""Check whether a cluster can actually run this project.

    python infra/preflight.py

Answers the question "is the free tier enough, or do I need Standard?" by
trying each capability rather than reasoning about tier documentation. Creates
its own scratch table and drops it again; safe to run against any cluster.

Exit code 0 means every hard requirement passed.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from bmem.db import load_env  # noqa: E402

load_env()

RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def report(self, ok: bool, name: str, detail: str = "", *, required: bool = True) -> None:
        if ok:
            mark, colour = "PASS", GREEN
        elif required:
            mark, colour = "FAIL", RED
            self.failures.append(name)
        else:
            mark, colour = "WARN", YELLOW
            self.warnings.append(name)
        line = f"  {colour}{mark}{RESET}  {name}"
        if detail:
            line += f"\n        {DIM}{detail}{RESET}"
        print(line)


def main() -> int:
    dsn = os.environ.get("BMEM_DATABASE_URL")
    if not dsn:
        print("BMEM_DATABASE_URL is not set.", file=sys.stderr)
        print(
            "  ccloud cluster sql <cluster-name> --connection-string\n"
            "  (or, for a local node: postgresql://root@localhost:26257/defaultdb?sslmode=disable)",
            file=sys.stderr,
        )
        return 1

    check = Check()
    scratch = f"preflight_{uuid.uuid4().hex[:8]}"

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        print(f"\n{DIM}{version}{RESET}\n")

        # ------------------------------------------------ vector indexing
        # The headline requirement. Branch-scoped retrieval is the project.
        try:
            cur.execute("SET CLUSTER SETTING feature.vector_index.enabled = true")
            check.report(True, "SET CLUSTER SETTING feature.vector_index.enabled")
        except psycopg.Error as exc:
            # Not necessarily fatal: newer versions may have graduated the
            # setting, in which case the index creation below still succeeds.
            check.report(
                False,
                "SET CLUSTER SETTING feature.vector_index.enabled",
                f"{type(exc).__name__}: {str(exc).strip()[:160]}",
                required=False,
            )

        try:
            cur.execute(
                f"""
                CREATE TABLE {scratch} (
                    branch_id UUID NOT NULL,
                    key STRING NOT NULL,
                    embedding VECTOR(1024),
                    PRIMARY KEY (branch_id, key)
                )
                """
            )
            check.report(True, "CREATE TABLE with a VECTOR(1024) column")
        except psycopg.Error as exc:
            check.report(False, "VECTOR column type", str(exc).strip()[:160])
            return finish(check)

        # The prefix column is what scopes an ANN search to one lineage at the
        # index level. Without it the isolation still holds, but every search
        # scans the whole corpus.
        try:
            cur.execute(f"CREATE VECTOR INDEX ON {scratch} (branch_id, embedding)")
            check.report(True, "CREATE VECTOR INDEX with a branch_id prefix column")
        except psycopg.Error as exc:
            check.report(
                False,
                "CREATE VECTOR INDEX (prefix column)",
                str(exc).strip()[:160],
            )

        # ------------------------------------------------ time travel window
        # Revert and audit read through AS OF SYSTEM TIME, which only reaches
        # back as far as gc.ttlseconds. The default is 4h; we want 24h.
        try:
            cur.execute(f"ALTER TABLE {scratch} CONFIGURE ZONE USING gc.ttlseconds = 86400")
            check.report(True, "CONFIGURE ZONE gc.ttlseconds (widen the AOST window)")
        except psycopg.Error as exc:
            check.report(
                False,
                "CONFIGURE ZONE gc.ttlseconds",
                f"{str(exc).strip()[:160]}\n        "
                f"Time travel will be limited to the default window (~4h). "
                f"Revert still works via the materialized snapshot.",
                required=False,
            )

        # ------------------------------------------------ row-level TTL
        try:
            cur.execute(
                f"CREATE TABLE {scratch}_ttl (id UUID PRIMARY KEY) "
                f"WITH (ttl_expire_after = '24 hours')"
            )
            check.report(True, "Row-level TTL (self-reaping abandoned branches)")
            cur.execute(f"DROP TABLE {scratch}_ttl")
        except psycopg.Error as exc:
            check.report(False, "Row-level TTL", str(exc).strip()[:160], required=False)

        # ------------------------------------------------ AOST
        try:
            cur.execute("SELECT cluster_logical_timestamp()")
            hlc = cur.fetchone()[0]
            cur.execute(f"SELECT count(*) FROM {scratch} AS OF SYSTEM TIME {hlc}")
            cur.fetchone()
            check.report(True, "AS OF SYSTEM TIME with an HLC literal")
        except psycopg.Error as exc:
            check.report(False, "AS OF SYSTEM TIME", str(exc).strip()[:160])

        # ------------------------------------------------ changefeeds
        # Drives the live branch tree. Not load-bearing for correctness -- the
        # UI can fall back to polling.
        try:
            cur.execute("SHOW CLUSTER SETTING kv.rangefeed.enabled")
            rangefeed = cur.fetchone()[0]
            check.report(
                bool(rangefeed),
                "kv.rangefeed.enabled (prerequisite for changefeeds)",
                "" if rangefeed else "changefeeds unavailable; the UI can poll instead",
                required=False,
            )
        except psycopg.Error as exc:
            check.report(False, "rangefeed check", str(exc).strip()[:160], required=False)

        cur.execute(f"DROP TABLE IF EXISTS {scratch}")

    return finish(check)


def finish(check: Check) -> int:
    print()
    if check.failures:
        print(f"{RED}Not usable as-is.{RESET} Hard requirements failed:")
        for name in check.failures:
            print(f"  - {name}")
        print(
            "\nIf this is a Basic (free) cluster, upgrade it to Standard -- the "
            "$400 hackathon credits cover roughly three months at the 2-vCPU rate."
        )
        return 1

    if check.warnings:
        print(f"{GREEN}Usable.{RESET} With degraded extras:")
        for name in check.warnings:
            print(f"  - {name}")
        return 0

    print(f"{GREEN}Fully usable.{RESET} Every capability this project needs is available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
