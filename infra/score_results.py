#!/usr/bin/env python3
"""Turn a JUnit XML report into a branch score.

Runs inside the CodeBuild sandbox, uploaded alongside the candidate patch.

    python score_results.py results.xml result.json [--reason ok]

Kept as a standalone file rather than inlined into the buildspec for two
reasons: a heredoc nested inside a YAML block scalar carries the block's
indentation into the interpreter and fails with IndentationError, and a separate
file can be unit-tested (see ``tests/test_scoring.py``).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
from typing import Any

__all__ = ["score_junit"]


def score_junit(xml_text: str) -> dict[str, Any]:
    """Parse a JUnit report into ``{passed, total, score, reason}``.

    Never raises. An unparseable or empty report scores zero with the cause
    recorded -- a branch whose test run fell over should lose, not crash the
    orchestrator that is waiting on it.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return {"passed": 0, "total": 0, "score": 0.0, "reason": f"unparseable_report: {exc}"}

    # pytest emits <testsuites><testsuite>...; some versions emit a bare
    # <testsuite>. Handle both.
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        return {"passed": 0, "total": 0, "score": 0.0, "reason": "no_testsuite_in_report"}

    def count(attr: str) -> int:
        return sum(int(s.get(attr, 0) or 0) for s in suites)

    total = count("tests")
    # Skipped tests did not run, so they belong in neither numerator nor
    # denominator -- counting them as passes would let a branch score well by
    # skipping everything it could not handle.
    skipped = count("skipped")
    failed = count("failures") + count("errors")
    considered = total - skipped
    passed = considered - failed

    if considered <= 0:
        return {
            "passed": 0,
            "total": 0,
            "score": 0.0,
            "reason": "no_tests_ran" if total == 0 else "all_tests_skipped",
        }

    return {
        "passed": passed,
        "total": considered,
        "score": passed / considered,
        "reason": "ok",
    }


_ERROR_LINE = re.compile(
    r"(No module named|ModuleNotFoundError|ImportError|SyntaxError|Error:|ERROR|error:)"
)


def explain_missing_report(log_path: pathlib.Path) -> str:
    """Why pytest wrote no report, from its own output.

    A bare "no_report_produced" hides the one fact needed to fix it -- the
    first real error line (typically an import failure while collecting) --
    behind a CloudWatch log nobody opens. Never raises.
    """
    try:
        text = log_path.read_text(errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    except OSError:
        return ""
    for line in lines:
        if _ERROR_LINE.search(line):
            return line[:240]
    return lines[-1][:240] if lines else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="path to the JUnit XML report")
    parser.add_argument("out", help="path to write result.json")
    parser.add_argument(
        "--reason",
        default=None,
        help="override the outcome, e.g. patch_did_not_apply",
    )
    parser.add_argument(
        "--log",
        default="/tmp/pytest.log",
        help="pytest output, used to explain a missing report",
    )
    parser.add_argument(
        "--base-sha",
        default=None,
        help="commit the target repo was checked out to before the patch was applied -- "
        "lets a later apply step detect that the repository has since moved",
    )
    args = parser.parse_args()

    if args.reason:
        result = {"passed": 0, "total": 0, "score": 0.0, "reason": args.reason}
    else:
        report = pathlib.Path(args.report)
        if not report.exists():
            why = explain_missing_report(pathlib.Path(args.log))
            reason = f"no_report_produced: {why}" if why else "no_report_produced"
            result = {"passed": 0, "total": 0, "score": 0.0, "reason": reason}
        else:
            result = score_junit(report.read_text())

    if args.base_sha:
        result["base_sha"] = args.base_sha

    pathlib.Path(args.out).write_text(json.dumps(result))
    print(
        f"score {result['score']:.3f} ({result['passed']}/{result['total']}) {result['reason']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
