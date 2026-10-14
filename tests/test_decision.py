"""bmem.decision's pure text-generation piece. get_decision_evidence() itself
needs a live cluster with real merge/branch/memory rows, so it's exercised
live rather than mocked (see demo/script.md) -- pr_description() is the part
that's meaningfully offline-testable: given evidence, does it faithfully
reflect it, never inventing anything evidence doesn't contain.
"""

from __future__ import annotations

from bmem.decision import AlternativeStrategy, DecisionEvidence, LatestApplication, pr_description


def _evidence(**overrides: object) -> DecisionEvidence:
    defaults: dict[str, object] = dict(
        merge_id="m-1",
        parent_id="p-1",
        winner_id="w-1",
        winning_hypothesis="Wrap in an ASGI shim",
        winning_score=0.87,
        belief="An ASGI shim keeps the existing routes working.",
        merged_at="2026-08-14T10:00:00+00:00",
        reverted_at=None,
        patch_available=True,
        base_sha="abc1234",
        alternatives=[],
        latest_application=None,
    )
    defaults.update(overrides)
    return DecisionEvidence.model_validate(defaults)


class TestPrDescription:
    def test_includes_the_winning_hypothesis_and_belief(self) -> None:
        text = pr_description(_evidence())
        assert "Wrap in an ASGI shim" in text
        assert "An ASGI shim keeps the existing routes working" in text

    def test_includes_the_score_as_a_percentage(self) -> None:
        text = pr_description(_evidence(winning_score=0.87))
        assert "87%" in text

    def test_missing_score_says_so_rather_than_fabricating_one(self) -> None:
        text = pr_description(_evidence(winning_score=None))
        assert "no score on record" in text
        assert "%" not in text.split("Motivation")[1].split("Winning strategy")[0]

    def test_lists_each_alternative_with_its_own_score(self) -> None:
        text = pr_description(
            _evidence(
                alternatives=[
                    AlternativeStrategy(
                        branch_id="b-2", hypothesis="Rewrite everything", status="abandoned",
                        score=0.42, belief="Too many breakages.",
                    ),
                ]
            )
        )
        assert "Rewrite everything" in text
        assert "42%" in text
        assert "Too many breakages." in text

    def test_no_alternatives_says_so_rather_than_omitting_the_section(self) -> None:
        text = pr_description(_evidence(alternatives=[]))
        assert "no sibling branches on record" in text

    def test_reports_unapplied_when_there_is_no_application_record(self) -> None:
        text = pr_description(_evidence(latest_application=None))
        assert "Not yet applied" in text

    def test_reports_real_validation_status_when_present(self) -> None:
        text = pr_description(
            _evidence(
                latest_application=LatestApplication(
                    validation_status="passed", branch="bmem/abc", commit_sha="deadbeef1234",
                    conflicts=[], created_at="2026-08-14T11:00:00+00:00",
                )
            )
        )
        assert "**passed**" in text
        assert "deadbeef1234" in text

    def test_provenance_section_has_the_decision_and_branch_ids(self) -> None:
        text = pr_description(_evidence(merge_id="DEC-42", winner_id="branch-99"))
        assert "DEC-42" in text
        assert "branch-99" in text

    def test_never_claims_a_patch_is_reviewed(self) -> None:
        # This is the one hardcoded editorial line in the generator -- pinned
        # by a test because a future edit silently dropping the disclaimer
        # would be exactly the kind of overclaiming the spec explicitly bans.
        text = pr_description(_evidence())
        assert "has not been human-reviewed" in text
