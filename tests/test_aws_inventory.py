"""AWS inventory for the dashboard, with fake S3/CodeBuild clients."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import bmem.aws_inventory as inv

_INDEX = {
    "runs": [
        {
            "run_id": "run1",
            "started_at": "2026-10-14T11:49:00+00:00",
            "branches": [
                {"branch_id": "b1", "strategy": "shim", "objects": []},
                {"branch_id": "b2", "strategy": "rewrite", "objects": []},
            ],
        }
    ],
    "merges": [{"merge_id": "m1", "parent_id": "root", "winner_id": "b1"}],
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BMEM_S3_BUCKET", "bucket")
    monkeypatch.setenv("BMEM_CODEBUILD_PROJECT", "proj")
    monkeypatch.setattr(
        inv,
        "_project_index",
        lambda root, max_runs: {
            "runs": [
                dict(r, branches=[dict(b, objects=[]) for b in r["branches"]])
                for r in _INDEX["runs"]
            ],
            "merges": list(_INDEX["merges"]),
        },
    )


class _S3:
    def __init__(self, keys: dict[str, int]) -> None:
        self.keys = keys

    def list_objects_v2(self, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        when = datetime(2026, 10, 14, tzinfo=UTC)
        return {
            "Contents": [
                {"Key": k, "Size": v, "LastModified": when, "StorageClass": "STANDARD"}
                for k, v in self.keys.items()
                if k.startswith(Prefix)
            ],
            "IsTruncated": False,
        }

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.keys[Key], "ContentType": "text/plain"}

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(b"x" * self.keys[Key])}


def test_storage_groups_live_objects_by_run_and_branch() -> None:
    s3 = _S3(
        {
            "runs/run1/b1/candidate.patch": 120,
            "runs/run1/b1/result.json": 40,
            "runs/run1/b2/apply.log": 30,
            "applied/m1/applied.bundle": 900,
            "runs/other/b9/candidate.patch": 5,
        }
    )
    out = inv.storage_inventory("root", s3=s3)
    b1, b2 = out["runs"][0]["branches"]
    assert [o["name"] for o in b1["objects"]] == ["candidate.patch", "result.json"]
    assert [o["name"] for o in b2["objects"]] == ["apply.log"]
    assert out["applied"][0]["merge_id"] == "m1"
    assert out["totals"] == {"objects": 4, "bytes": 1090}
    assert out["error"] is None


def test_s3_failure_is_reported_not_raised() -> None:
    class Denied:
        def list_objects_v2(self, **_):
            raise PermissionError("AccessDenied")

    out = inv.storage_inventory("root", s3=Denied())
    assert out["error"].startswith("S3: PermissionError")


class _CodeBuild:
    def list_builds_for_project(self, **_):
        return {"ids": ["proj:1", "proj:2"]}

    def batch_get_builds(self, ids):
        def build(i, run, branch):
            return {
                "id": i,
                "buildNumber": 7,
                "buildStatus": "SUCCEEDED",
                "environment": {
                    "environmentVariables": [
                        {"name": "RUN_ID", "value": run},
                        {"name": "BRANCH_ID", "value": branch},
                    ]
                },
                "phases": [
                    {"phaseType": "BUILD", "phaseStatus": "SUCCEEDED", "durationInSeconds": 300}
                ],
            }

        return {"builds": [build("proj:1", "run1", "b1"), build("proj:2", "someone-else", "zz")]}


def test_builds_are_matched_to_this_projects_branches() -> None:
    out = inv.codebuild_builds("root", codebuild=_CodeBuild())
    assert [b["id"] for b in out["builds"]] == ["proj:1"]
    assert out["builds"][0]["strategy"] == "shim"
    assert out["builds"][0]["phases"][0]["seconds"] == 300


def test_read_object_is_restricted_to_app_prefixes() -> None:
    s3 = _S3({"runs/run1/b1/apply.log": 10, "secrets/key": 10, "runs/big": 10_000_000})
    assert inv.read_object("runs/run1/b1/apply.log", s3=s3)[0] == b"x" * 10
    with pytest.raises(PermissionError):
        inv.read_object("secrets/key", s3=s3)
    with pytest.raises(PermissionError):
        inv.read_object("runs/../secrets/key", s3=s3)
    with pytest.raises(ValueError):
        inv.read_object("runs/big", s3=s3)
