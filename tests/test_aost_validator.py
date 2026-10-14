"""The AOST literal validator is the only place this codebase builds SQL by
concatenation, so it gets tested like a security control rather than a helper.

Runs with no cluster and no cloud account.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from bmem.db import InvalidHLC, aost_literal


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "123",
        "1755712043123456789",
        "1755712043123456789.0000000001",
        "1.0",
        "99999999999999999999.99999999999999999999",
    ],
)
def test_accepts_well_formed_hlc(value: str) -> None:
    assert aost_literal(value) == value


def test_accepts_decimal_and_int_from_the_driver() -> None:
    # psycopg hands DECIMAL columns back as Decimal, so the validator has to
    # take them without a lossy round-trip through float.
    assert aost_literal(Decimal("1755712043123456789.0000000001")) == (
        "1755712043123456789.0000000001"
    )
    assert aost_literal(1755712043) == "1755712043"


def test_large_decimal_does_not_reach_sql_as_scientific_notation() -> None:
    # Decimal("1E+19") formats as "1E+19" under str(), which is not valid here.
    # Normalising to fixed-point first is what keeps this from being rejected --
    # and, worse, from being accepted in some other formatting path.
    assert aost_literal(Decimal("1E+19")) == "10000000000000000000"


@pytest.mark.parametrize(
    "attack",
    [
        "1' OR '1'='1",
        "'; DROP TABLE memory; --",
        "1755712043 OR 1=1",
        "1755712043; SELECT pg_sleep(10)",
        "(SELECT max(pre_merge_hlc) FROM merge_log)",
        "1755712043/**/UNION/**/SELECT",
    ],
)
def test_rejects_injection(attack: str) -> None:
    with pytest.raises(InvalidHLC):
        aost_literal(attack)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "2024-01-01",
        "2024-01-01T00:00:00Z",
        "-1",
        "+1",
        "1e10",
        "1E+19",
        "1.2.3",
        ".5",
        "5.",
        "NaN",
        "Infinity",
        "-Infinity",
        " 123",
        "123 ",
        "123\n",
        "123\t456",
        "0x1f",
        "١٢٣",  # Arabic-Indic digits
        "１２３",  # fullwidth digits
        "३४५",  # Devanagari digits
    ],
)
def test_rejects_malformed(value: str) -> None:
    with pytest.raises(InvalidHLC):
        aost_literal(value)


def test_rejects_non_ascii_digits_that_a_naive_check_would_pass() -> None:
    """Guards the specific reason the pattern is ``[0-9]`` and not ``\\d``.

    Python's ``\\d`` matches every Unicode decimal digit, so an Arabic-Indic
    numeral passes a ``\\d``-based regex, survives ``str.isdigit()``, and even
    converts cleanly via ``int()`` -- then reaches CockroachDB as bytes it will
    not parse. This test fails loudly if anyone "simplifies" the pattern.
    """
    arabic_indic = "١٧٥٥٧١٢٠٤٣"

    assert arabic_indic.isdigit()  # a naive check says yes
    assert re.fullmatch(r"\d+", arabic_indic)  # so does a naive regex
    assert int(arabic_indic) == 1755712043  # and so does int()

    with pytest.raises(InvalidHLC):  # this validator does not
        aost_literal(arabic_indic)


@pytest.mark.parametrize("value", [None, True, False, 1.5, [], {}, object()])
def test_rejects_wrong_types(value: object) -> None:
    # bool matters specifically: it subclasses int, so True would otherwise
    # validate as "1" and silently time-travel to the epoch.
    with pytest.raises(InvalidHLC):
        aost_literal(value)  # type: ignore[arg-type]


def test_error_message_does_not_execute_or_reformat_the_input() -> None:
    with pytest.raises(InvalidHLC) as excinfo:
        aost_literal("'; DROP TABLE memory; --")
    assert "DROP TABLE" in str(excinfo.value)  # reported verbatim, for the log
