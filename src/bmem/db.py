"""Database access, HLC handling, and the one place SQL is ever interpolated.

CockroachDB rejects placeholders in ``AS OF SYSTEM TIME``: the timestamp must be
a literal embedded in the statement text. Every other query in this codebase is
fully parameterised; time-travel queries cannot be, so they all funnel through
:func:`aost_literal`, which is the single interpolation site and the only thing
standing between a caller-supplied timestamp and the SQL string.
"""

from __future__ import annotations

import os
import random
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.adapt import Dumper
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

__all__ = [
    "HLC",
    "InvalidHLC",
    "Vector",
    "aost_literal",
    "cluster_logical_timestamp",
    "follower_read",
    "get_pool",
    "load_env",
    "run_transaction",
    "time_travel",
    "transaction",
]


def load_env() -> None:
    """Load ``.env`` from the repository root, without overriding real env vars.

    Called on import so scripts and tests pick up the connection string with no
    ceremony. ``override=False`` matters: an explicitly exported variable, or one
    injected by CodeBuild or App Runner, must win over a developer's local file.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # dotenv is a convenience, not a requirement
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    load_dotenv(os.path.join(root, ".env"), override=False)


load_env()

# An HLC value as it crosses our own boundaries. CockroachDB hands these back as
# DECIMAL; we keep them as strings so nothing silently loses precision on the way
# to a SQL literal.
HLC = str


class InvalidHLC(ValueError):
    """A value was offered as a hybrid logical clock timestamp and was not one."""


# Deliberately [0-9] and not \d.
#
# In Python, `\d` on a str pattern matches every Unicode decimal digit --
# including Arabic-Indic (٣), Devanagari (३), and fullwidth (３). Those pass a
# naive digit check, survive int() and Decimal(), and reach the SQL string as
# bytes CockroachDB will not parse. `[0-9]` admits ASCII only.
#
# Bounds: CockroachDB's HLC is nanoseconds since the Unix epoch (19 digits
# today, 20 for the next few millennia) with a logical counter after the point.
_HLC_PATTERN = re.compile(r"^[0-9]{1,20}(\.[0-9]{1,20})?$")


def aost_literal(value: HLC | Decimal | int) -> str:
    """Validate an HLC timestamp and return it as a bare SQL literal.

    This is the **only** function in the codebase permitted to produce text that
    is concatenated into a SQL statement. It is deliberately strict: it accepts
    an unsigned decimal number and nothing else -- no signs, no exponents, no
    whitespace, no non-ASCII digits, no SQL, no dates.

    Callers must pass a value that originated from ``cluster_logical_timestamp()``
    or from a ``DECIMAL`` column that holds one. If a value ever fails here it
    means untrusted input reached a time-travel query, so this raises rather than
    falling back to a default timestamp -- silently reading "now" instead of the
    requested instant would turn an attack into a plausible-looking wrong answer.

    >>> aost_literal("1755712043123456789.0000000001")
    '1755712043123456789.0000000001'
    >>> aost_literal("1' OR '1'='1")
    Traceback (most recent call last):
    bmem.db.InvalidHLC: not a hybrid logical clock timestamp: "1' OR '1'='1"
    """
    if isinstance(value, bool):
        # bool is an int subclass; True would otherwise validate as "1".
        raise InvalidHLC(f"not a hybrid logical clock timestamp: {value!r}")

    if isinstance(value, (Decimal, int)):
        # Decimal may format as scientific notation ("1E+19"), which the pattern
        # correctly rejects. Normalise through a fixed-point representation first.
        text = format(value, "f") if isinstance(value, Decimal) else str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise InvalidHLC(f"not a hybrid logical clock timestamp: {value!r}")

    if not _HLC_PATTERN.fullmatch(text):
        raise InvalidHLC(f"not a hybrid logical clock timestamp: {text!r}")

    return text


class Vector:
    """A sequence of floats destined for a CockroachDB ``VECTOR`` column.

    Binding matters here in a way it does not for ordinary values. A bare Python
    list already means *array* to psycopg, and this codebase binds real arrays
    elsewhere (``ANY(%(ids)s)``, ``!= ALL(%(keys)s)``). If a list also meant
    *vector*, one of the two would have to serialize wrongly. Wrapping keeps them
    unambiguous: only a ``Vector`` becomes the ``[a,b,c]`` literal the VECTOR
    type parses, and everything else stays an array.

    ``repr(float(v))`` gives the shortest string that round-trips the double, so
    an embedding survives the trip to SQL and back without drifting.
    """

    __slots__ = ("literal",)

    def __init__(self, values: Sequence[float]) -> None:
        self.literal = "[" + ",".join(repr(float(v)) for v in values) + "]"


class _VectorDumper(Dumper):
    """Serialize a :class:`Vector` as its text literal.

    The OID is left unset (unknown), so CockroachDB casts the literal to VECTOR
    from context -- the target column on an UPSERT, or the other operand of the
    ``<->`` distance operator on a search. That is exactly how a hand-written
    ``embedding <-> '[1,2,3]'`` resolves.
    """

    def dump(self, obj: Vector) -> bytes:
        return obj.literal.encode()


def _register_vector(conn: psycopg.Connection[Any]) -> None:
    conn.adapters.register_dumper(Vector, _VectorDumper)


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Return the process-wide connection pool, opening it on first use."""
    global _pool
    if _pool is None:
        dsn = os.environ.get("BMEM_DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "BMEM_DATABASE_URL is not set. Copy .env.example to .env and fill in "
                "the connection string from `ccloud cluster sql <cluster>`."
            )
        _pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=16,
            kwargs={"row_factory": dict_row, "application_name": "branchable-memory"},
            configure=_register_vector,
            open=True,
        )
    return _pool


@contextmanager
def transaction() -> Iterator[psycopg.Cursor[Any]]:
    """Run a block inside one serializable transaction.

    CockroachDB is SERIALIZABLE by default, so a merge and the retirement of its
    losing siblings either both land or neither does. Nothing here weakens that.
    """
    with get_pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        yield cur


@contextmanager
def time_travel(hlc: HLC | Decimal | int) -> Iterator[psycopg.Cursor[Any]]:
    """Run a block reading the database as it existed at ``hlc``.

    CockroachDB fixes a transaction's timestamp when the transaction begins, and
    ``transaction()`` above always begins one. A statement-level ``AS OF SYSTEM
    TIME`` inside it is therefore rejected outright -- "inconsistent AS OF SYSTEM
    TIME timestamp; expected <now>, got <hlc>" -- even when it is the first
    statement, because BEGIN has already chosen. The clause has to set the
    timestamp for the whole transaction instead, which is precisely what the
    server's own HINT on that error recommends.

    Found empirically: the statement-level form passes every offline test and
    fails against a real cluster every time, so it is only visible under
    ``pytest -m cluster``.

    The timestamp is validated by :func:`aost_literal` here rather than by the
    caller, which keeps interpolation confined to this module.
    """
    literal = aost_literal(hlc)
    with get_pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        # literal came from aost_literal(), which admits nothing but a bare
        # unsigned decimal -- see its docstring for why that is the whole
        # defence here.
        cur.execute(f"SET TRANSACTION AS OF SYSTEM TIME {literal}")  # noqa: S608
        yield cur


@contextmanager
def follower_read() -> Iterator[psycopg.Cursor[Any]]:
    """Run a read-only block a few seconds in the past, guaranteed not to
    contend with concurrent writes.

    Found empirically, chasing a real bug: the live UI polls ``_snapshot()``
    (a recursive CTE over ``branches``) once a second for as long as a browser
    tab is connected, over the same table ``fork()`` and ``merge()`` write to.
    Under CockroachDB's default SERIALIZABLE isolation that poll is a genuine
    transaction participating in conflict detection like any other -- and
    because it never stops, it is not a rare collision but a standing one,
    given a long enough retry loop to eventually land on. Every fork attempted
    from a plain script with no browser tab open succeeded; every one attempted
    through the real UI, with the tree view open and polling, failed with
    ``RETRY_SERIALIZABLE`` after exhausting all 8 attempts in
    :func:`run_transaction`. Same code, same cluster, same trunk -- the only
    variable was whether something was continuously reading the table the
    fork was writing to.

    ``follower_read_timestamp()`` is CockroachDB's documented answer to
    exactly this shape of problem: it reads from a follower replica as of a
    timestamp old enough (a few seconds) to be outside the window any
    in-flight write could still be contending over, so this transaction
    simply never enters the same conflict graph as ``fork()`` or ``merge()``.
    A live tree view that lags reality by a couple of seconds is a fair
    trade against making the one write path an open SSE connection can
    reliably starve.

    No caller-supplied value reaches the SQL here -- ``follower_read_timestamp()``
    is a fixed builtin call, not a value needing :func:`aost_literal`'s
    validation -- so this needs no interpolation guard of its own.
    """
    with get_pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("SET TRANSACTION AS OF SYSTEM TIME follower_read_timestamp()")
        yield cur


_RETRY_MAX_ATTEMPTS = 8
_RETRY_BASE_DELAY = 0.05
_RETRY_MAX_DELAY = 2.0


def run_transaction[T](
    fn: Callable[[psycopg.Cursor[Any]], T], *, max_attempts: int = _RETRY_MAX_ATTEMPTS
) -> T:
    """Run ``fn(cur)`` in a transaction, retrying the whole thing on SQLSTATE 40001.

    CockroachDB's own transaction-retry-error docs are explicit that this is not
    a sign of a bug: a transaction can be asked to restart from a stale read even
    with no contention at all, purely because it stayed open long enough to
    outlive its closed timestamp interval. The documented fix is "redo the entire
    transaction from BEGIN", not "retry the commit" -- which is why this takes a
    function rather than being another way to open the ``transaction()`` context
    manager: on retry, every statement ``fn`` issued runs again from scratch, so
    ``fn`` must be safe to call more than once (as an idempotent UPSERT keyed on
    a stable key already is).

    Found empirically: an ingest of ~130 UPSERTs in one transaction hit this
    reliably across the high-latency link between a dev laptop and the cluster's
    region, because 130 sequential round trips is long enough to age out. Inside
    the target AWS region, per-statement latency drops enough that this stops
    being the common case -- but CockroachDB's own docs are clear that it is
    never impossible, so every multi-statement transaction in this codebase goes
    through here rather than the bare context manager.

    Full jitter, not base-plus-jitter: sleep a uniformly random duration between
    zero and the exponentially growing cap, rather than always waiting at least
    the base delay. Found necessary empirically too -- two branches racing to
    merge into the same parent (``tests/test_merge_revert.py``'s
    ``test_concurrent_merges_produce_exactly_one_winner``) both started at
    essentially the same instant kept colliding on retry after retry, because a
    delay that is always "at least X" leaves two synchronized callers only the
    jitter's width to drift apart by. Full jitter gives the *whole* delay to
    chance, so the two racers' next attempts land at genuinely uncorrelated
    times. This is https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/'s
    "full jitter", not picked arbitrarily.
    """
    for attempt in range(max_attempts):
        try:
            with transaction() as cur:
                return fn(cur)
        except psycopg.errors.SerializationFailure:
            if attempt == max_attempts - 1:
                raise
            cap = min(_RETRY_MAX_DELAY, _RETRY_BASE_DELAY * (2**attempt))
            time.sleep(random.uniform(0, cap))
    raise AssertionError("unreachable: loop above always returns or raises")


def cluster_logical_timestamp(cur: psycopg.Cursor[Any]) -> HLC:
    """Read the cluster's current HLC, as a string, on an open cursor.

    Taken inside the caller's transaction so the value is consistent with the
    writes around it.
    """
    cur.execute("SELECT cluster_logical_timestamp() AS hlc")
    row = cur.fetchone()
    assert row is not None  # a scalar SELECT always returns a row
    return format(row["hlc"], "f")
