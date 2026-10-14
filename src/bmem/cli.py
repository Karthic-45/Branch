"""A small CLI over the same reads the API and UI use.

Four commands, matching the spec's own priority: list decisions, inspect one,
fetch its patch, and export a report. Everything here goes through the same
functions the HTTP layer calls, so the CLI cannot drift from the UI or report
a different answer than the audit surface would.

Read-only by design. Forking, merging, reverting and applying stay behind the
API, where the demo drives them deliberately.

    bmem decisions <root-id>
    bmem decision  <merge-id>
    bmem patch     <merge-id> [-o out.patch]
    bmem report    <merge-id|root-id> [--kind decision|executive|engineering|archive]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bmem.decision import decision_graph, get_counterfactual, get_decision_evidence

__all__ = ["main"]


def _print_decisions(root_id: str, as_json: bool) -> int:
    nodes = decision_graph(root_id)
    if as_json:
        print(json.dumps([n.model_dump() for n in nodes], indent=2, default=str))
        return 0
    if not nodes:
        print(f"No decisions recorded under {root_id}.", file=sys.stderr)
        return 1
    for n in nodes:
        score = f"{n.winner_score:.0%}" if n.winner_score is not None else "—"
        flags = []
        if n.tie_detected:
            flags.append("tie-broken")
        if n.reverted_at:
            flags.append("reverted")
        if n.applied_commit_sha:
            flags.append(f"applied {n.applied_commit_sha[:8]}")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        title = n.winner_hypothesis or n.winner_branch_id[:8]
        print(f"{n.merge_id[:8]}  {score:>5}  {title}{suffix}")
    return 0


def _print_decision(merge_id: str, as_json: bool) -> int:
    evidence = get_decision_evidence(merge_id)
    cf = get_counterfactual(merge_id)
    if as_json:
        print(json.dumps(
            {"evidence": evidence.model_dump(), "counterfactual": cf.model_dump()},
            indent=2, default=str,
        ))
        return 0

    print(f"Decision {evidence.merge_id}")
    print(f"  winner    {evidence.winning_hypothesis or evidence.winner_id}")
    score = (
        f"{evidence.winning_score:.1%}"
        if evidence.winning_score is not None
        else "not recorded"
    )
    print(f"  score     {score}")
    print(f"  merged    {evidence.merged_at or '—'}")
    print(f"  state     {'reverted' if evidence.reverted_at else 'in effect'}")
    print(f"  base sha  {evidence.base_sha or 'not recorded'}")
    print(f"\n  belief    {evidence.belief or '(none recorded)'}")
    print(f"\n  why       {cf.rationale}")
    if cf.alternatives:
        print("\n  rejected:")
        for a in cf.alternatives:
            alt_score = f"{a.score:.0%}" if a.score is not None else "—"
            print(f"    {alt_score:>5}  {a.hypothesis or a.strategy_name}")
            for point in a.points:
                print(f"           - {point}")
    return 0


def _write_patch(merge_id: str, out: str | None) -> int:
    # Imported here rather than at module scope: fetching a patch needs boto3
    # and AWS configuration, and `bmem decisions` should keep working on a
    # machine that has neither.
    import os

    import boto3

    from bmem.db import transaction

    with transaction() as cur:
        cur.execute("SELECT winner_id FROM merge_log WHERE id = %(id)s", {"id": merge_id})
        row = cur.fetchone()
        if row is None:
            print(f"No such merge: {merge_id}", file=sys.stderr)
            return 1
        cur.execute(
            "SELECT s3_uri FROM memory WHERE branch_id = %(b)s AND s3_uri IS NOT NULL LIMIT 1",
            {"b": row["winner_id"]},
        )
        patch_row = cur.fetchone()

    if patch_row is None or not patch_row["s3_uri"]:
        print(
            f"No patch on record for {merge_id} -- merged before patch tracking existed, "
            "or scored with a fake evaluator.",
            file=sys.stderr,
        )
        return 1

    bucket, _, key = patch_row["s3_uri"].removeprefix("s3://").partition("/")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    if out:
        Path(out).write_bytes(body)
        print(f"Wrote {len(body)} bytes to {out}", file=sys.stderr)
    else:
        sys.stdout.buffer.write(body)
    return 0


def _write_report(subject: str, kind: str, out: str | None) -> int:
    from bmem.report import decision_pdf, engineering_pdf, executive_pdf, project_archive

    if kind == "archive":
        content, digest = project_archive(subject)
        default = f"branchable-memory-{subject[:8]}.zip"
    else:
        builder = {
            "decision": decision_pdf,
            "executive": executive_pdf,
            "engineering": engineering_pdf,
        }[kind]
        bundle = builder(subject)
        content, digest, default = bundle.content, bundle.sha256, bundle.filename

    path = out or default
    Path(path).write_bytes(content)
    print(f"Wrote {len(content)} bytes to {path}", file=sys.stderr)
    print(f"sha256 {digest}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bmem", description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("decisions", help="list every decision under a root branch")
    p.add_argument("root_id")

    p = sub.add_parser("decision", help="show one decision and why it won")
    p.add_argument("merge_id")

    p = sub.add_parser("patch", help="fetch a decision's winning patch")
    p.add_argument("merge_id")
    p.add_argument("-o", "--out", help="write here instead of stdout")

    p = sub.add_parser("report", help="generate a PDF or archive")
    p.add_argument("subject", help="merge id for a decision report, root id otherwise")
    p.add_argument(
        "--kind",
        default="decision",
        choices=("decision", "executive", "engineering", "archive"),
    )
    p.add_argument("-o", "--out")

    args = parser.parse_args(argv)

    handlers: dict[str, Any] = {
        "decisions": lambda: _print_decisions(args.root_id, args.json),
        "decision": lambda: _print_decision(args.merge_id, args.json),
        "patch": lambda: _write_patch(args.merge_id, args.out),
        "report": lambda: _write_report(args.subject, args.kind, args.out),
    }
    try:
        return int(handlers[args.command]())
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
