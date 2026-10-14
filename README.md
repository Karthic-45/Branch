# Branchable Memory

**Version control for what an agent believes.**

Agent memory today is append-only. When an agent reaches a bad conclusion it does not delete that conclusion — it appends a correction and hopes the model attends to the newer text. The discredited note stays semantically relevant, so retrieval keeps surfacing it, and a wrong belief formed in step 4 quietly poisons steps 5 through 40.

This project gives an agent memory that **forks, competes, merges, and reverts**. An agent can fork its entire memory state, explore contradictory hypotheses in parallel with genuine isolation between them, score each against a real-world outcome, merge the winner, and let the losers disappear from retrieval entirely.

Built on CockroachDB, deployed on AWS. Submission for the **CockroachDB × AWS Hackathon — Build with Agentic Memory**.

**No hosted environment is running.** [Running it](#running-it) is two commands against the live CockroachDB cluster, and [`infra/deploy_beanstalk.sh`](infra/deploy_beanstalk.sh) ships the same container to a public URL when one is wanted. A URL is not listed here because there is not one to list — see [Honest limitations](#honest-limitations).

---

## The claim, and how to check it

> Branch B cannot retrieve Branch A's conclusions. Same database, same vector index, same query — a different belief state.

That is the whole project, and it is mechanically verified in [`tests/test_isolation.py`](tests/test_isolation.py): write a plausible-but-wrong belief to one branch, run the identical semantic search from a sibling, assert zero results — then assert the originating branch *can* still see it, so the test cannot pass by the search being broken.

## How it works

**A branch is lineage, not a copy.** Forking writes one row — a parent pointer and the cluster's hybrid-logical-clock timestamp at the moment of the split. No memory is duplicated, so forking fifty hypotheses costs fifty small inserts.

**Reading resolves through the ancestry chain.** Each memory row carries the HLC at which it was written. A branch sees all of its own rows, plus each ancestor's rows *as that ancestor stood when the fork happened*. Walking up the chain assigns every ancestor a visibility horizon; a row is inherited only if it was written at or before that horizon. The result is copy-on-write isolation with zero copying.

**Retrieval is scoped to a belief state.** The vector index is declared `(branch_id, embedding)` — `branch_id` is a *prefix column*, which is what lets CockroachDB restrict an approximate-nearest-neighbour search to a lineage at the index level rather than post-filtering the whole corpus. This is the part no external vector store reproduces: four agents share one index without contaminating each other.

**Merging is one serializable transaction.** Promoting a winner and retiring its siblings happen together or not at all. There is no window in which two winners exist.

**Reverting is real.** Every merge records both the pre-merge timestamp and a materialized snapshot of the parent's belief state, so a merged conclusion can be rolled back to exactly what preceded it and a different branch promoted instead.

**Abandoned reasoning is reclaimed.** Branch scratch memory carries a row-level TTL. Within a day the discarded reasoning is gone from the database, so a future semantic search cannot surface it — because it does not exist.

## Watching it work

Starting a migration ([`POST /api/migrations`](src/bmem/api.py)) forks the requested count of branches, explores each in its own thread against Bedrock, evaluates each patch in CodeBuild, and merges the winner — the same [orchestrator](src/bmem/orchestrate.py) whether driven from the UI or a script. The [React frontend](web/) repaints live over SSE as the branches move from `exploring` to `merged` or `abandoned`, with a D3-laid-out tree and a nine-stage execution trace (Ingest → Plan → Fork → Retrieve → Reason → Upload → Build → Score → Merge). Every stage in that trace is derived from the event feed alone — nothing advances on a timer, so a stalled CodeBuild reads as still running rather than quietly completing.

The UI is a control plane, not a dashboard: **Overview** (what is happening now), **Decisions** (master-detail, six tabs per decision), **Branches**, **Beliefs**, **Deployments**, **Observability**, **Proofs**, **Reports** and **Audit**, with ⌘K over the real decisions and branches in the store. The honesty rule is carried into the primitives — a value the database has no record of renders as *Not recorded*, never as zero, and a pending fetch renders as a skeleton so it can never be mistaken for an absent value.

**Beliefs** answers the question an auditor asks first: show me everything this branch can see, and tell me which of it the branch concluded itself. It reports visible rows against own rows — a branch showing 7 visible and 1 own is the copy-on-write claim in a form you can check against the cluster, because the other 6 exist once, on an ancestor, and were never copied. Filtering there is a plain substring match rather than a vector search, deliberately: this surface is for auditing what is present, and ranking could silently drop a row out of view.

Every stage in between is visible too, not just the endpoints. `run_migration()` and everything it calls accept an `on_event(branch_id, stage, message)` callback, threaded through planning, forking, each branch's explore/evaluate cycle — including CodeBuild's real build phases (`PROVISIONING`, `DOWNLOAD_SOURCE`, `BUILD`, …), read straight off the CodeBuild API rather than invented — and the merge. The UI's **Activity** panel is that callback's output streamed over SSE, and each branch card's five-step tracker (Fork → Explore → Evaluate → Score → Merge) reads the same feed to show which stage a branch is on right now, not just its final status.

The merge log underneath it is append-only and drives **Revert**: undoing a merge restores the parent's exact pre-merge belief state and leaves the log entry in place, marked reverted rather than deleted — the record that a conclusion was reached and later undone survives.

The same store is queryable in natural language through [`bmem-audit`](src/bmem/mcp_server.py), a nine-tool read-only MCP server — `list_branches`, `explain_branch`, `search_branch_memory`, `list_merges`, `beliefs_before_merge`, plus `explain_decision`, `get_project_timeline`, `get_reproducibility_manifest` and `get_known_failures` — none of which can fork, merge, or revert anything. [`tests/test_mcp_audit.py`](tests/test_mcp_audit.py) fails the build if any tool's source ever contains a writing SQL statement. Point a client at it (`claude mcp add bmem-audit -- .venv/bin/python -m bmem.mcp_server`, or it's already in [`.mcp.json`](.mcp.json)) and ask it which hypothesis a branch abandoned, or what the parent believed immediately before a specific merge.

## From a decision to a change

A winning belief that nobody can act on is half a system, so the decision keeps going after the merge.

**The patch survives.** Each branch's candidate diff is stored in S3 and its location recorded on the winning memory row, so the actual code a strategy produced is downloadable long after the build container is gone.

**Applying is guarded.** [`bmem.apply`](src/bmem/apply.py) clones at the exact commit the patch was *scored against*, refuses to continue if the repository has moved since (`base_changed`), applies to an isolated branch, runs the repository's own [validation profile](src/bmem/apply.py) — install/lint/typecheck/build/test, configured per repo rather than assuming pytest — and commits locally. Every attempt is recorded, including the rejected ones. It never pushes and never opens a pull request: this deployment holds no write credential, and a PR body is generated for a human to paste rather than silently posted.

**Decisions explain themselves.** When two strategies score within one point, the run says so and records which tie-breaker chose the winner instead of presenting a coin flip as evidence. Losing branches are kept with their failure reasons and fed back to the planner on the next run against that parent, so the agent stops rediscovering the same dead ends. Every claim in the UI's *Why this one* view traces to a stored number — see [`bmem.decision`](src/bmem/decision.py).

**Runs are reproducible.** [`execution_manifests`](schema/008_execution_manifests.sql) pins the repository commit, model, prompt version, embedding model, test selector and the exact memories retrieved. A replay re-runs a strategy against that pinned commit on a *fresh sibling branch*, leaving the original untouched. A run backed by a stand-in records its model fields as absent rather than inventing a model name.

**Claims are executable.** The **Proofs** tab runs four demonstrations against the live cluster — branch isolation, copy-on-write forking, temporal memory, and resilience under injected worker failure — each reporting its steps and timings, each cleaning up after itself. Failures in the resilience proof are injected in application code only, never against the cluster or a real build.

**The record is exportable.** Decision, executive and engineering PDFs plus a complete ZIP archive ([`bmem.report`](src/bmem/report.py)), each carrying a SHA-256 of its own contents. There is also a small read-only CLI: `bmem decisions`, `bmem decision`, `bmem patch`, `bmem report`.

## Architecture

![Architecture: browser and an MCP client talk to a FastAPI control plane and a read-only MCP audit server, both backed by CockroachDB Cloud; the control plane also calls Bedrock for reasoning and embeddings, and drives S3 + CodeBuild for sandboxed evaluation, which writes scores back to CockroachDB.](docs/architecture.svg)

The live tree (top left) and the MCP audit surface (top right) are two independent read paths into the same store — one for a person watching the demo, one for an agent asking about it in natural language. Neither can write; only the FastAPI control plane forks, merges, or reverts.

**CockroachDB tools used** (the hackathon requires at least two of four):

| Tool | Role |
| --- | --- |
| Distributed Vector Indexing | Branch-scoped semantic retrieval; vectors and branch lineage in one query |
| Managed MCP Server | Raw SQL audit access, alongside this project's own domain-specific [`bmem-audit`](src/bmem/mcp_server.py) server (nine read-only tools: list/explain branches, search a branch's memory, list and reconstruct merges, explain a decision, walk the project timeline, read a reproducibility manifest, and list known failures) |
| `ccloud` CLI | Cluster provisioning and configuration ([`infra/provision_cluster.sh`](infra/provision_cluster.sh)); [`infra/preflight.py`](infra/preflight.py) verifies every capability the project needs before a single line of application code runs against it |

**AWS services:** Bedrock (Claude Sonnet 4.6 for reasoning, Titan Text Embeddings v2 for vectors), S3, CodeBuild, Elastic Beanstalk.

## The task it's grounded in

Correctness has to come from somewhere other than the model's own judgement, so the demo runs **automated framework migration** against [CTFd](https://github.com/CTFd/CTFd) — a real, Apache-2.0 Flask codebase with a large pytest suite. Measured at the ingested commit: **346 Python files, 48,541 lines, 1,512 embedded chunks** in the trunk branch. The agent proposes four migration strategies, forks four isolated memory branches, works them in parallel, and CI decides. Branch selection is a test suite grading the agent, not the agent grading itself.

To be precise about scope: **the whole repository is ingested and embedded**, but the migration task targets a bounded slice of it (the auth and routing layer). The **full** test suite runs against each candidate patch, so a strategy that breaks something elsewhere is scored honestly. This is a deliberate choice — a coherent full-repo rewrite is not something any current agent produces reliably, and pretending otherwise would make the demo a lie.

## Honest limitations

- **Time travel reaches back 24 hours, not forever.** `AS OF SYSTEM TIME` can only read within the garbage-collection window. We raise it from the 4-hour default to 24 hours, and store a materialized pre-merge snapshot so revert and audit keep working past it. The time-travel query is the elegant path; the snapshot is the one production depends on.
- **Retrieval does not use `AS OF SYSTEM TIME`.** It cannot: `AS OF SYSTEM TIME` fixes the timestamp of an entire *transaction*, not one statement — stricter than it first looks, since even a statement-level clause as the very first line of a transaction is rejected once `BEGIN` has already run (`SET TRANSACTION AS OF SYSTEM TIME` is the actual escape hatch; see [`time_travel()`](src/bmem/db.py)). An N-deep ancestry chain needing N distinct timestamps was never expressible under it either way. Branch-scoped reads use an HLC watermark column instead — one indexed query rather than N. AOST is reserved for revert and audit, where one timestamp against one branch is exactly what it supports — and even there, the timestamp has to be captured in a transaction that commits *before* the one it is meant to precede, or it names the merge's own writes instead of the instant before them.
- **Every AOST call is string-interpolated,** because CockroachDB rejects placeholders there. All of them route through one validator ([`aost_literal`](src/bmem/db.py)) that accepts an unsigned ASCII decimal and nothing else, called from the single function permitted to open a time-travel transaction. It is the only interpolation site in the codebase.
- **The offline stand-in for embeddings is deterministic but not semantic,** so it cannot stand in for retrieval *quality* — only for retrieval *scoping*. Against the 1,512-chunk corpus, a paraphrased query under `BMEM_FAKE_EMBEDDINGS=1` ranks arbitrarily; querying with a belief's exact text pins it at distance zero, which is what makes `tests/test_isolation.py`'s "not found" assertions mean "lineage excluded it" rather than "similarity was poor". Branch isolation is enforced by lineage, not distance, so it is fully testable offline — but a natural-language search demo needs Titan live.
- **Nothing here deploys itself, and no pull request is ever opened.** The apply engine stops at a local commit; deployment and rollback are *recorded*, not performed, and the record exists so "which decision is live" has an answer. Both boundaries are deliberate: an agent that can promote its own belief and then ship it unattended is a much larger safety claim than this project makes, and opening a PR would need a write-scoped credential this deployment does not hold.
- **Cost is reported as measured usage, not dollars.** Embedding calls, builds and reasoning calls are counted exactly; a dollar figure appears only if you configure `BMEM_PRICE_*` rates. AWS pricing varies by region, contract and free-tier position, and a plausible-looking invented total would be the same class of fabrication this project refuses elsewhere. S3 object sizes are not tracked at all and say so.
- **Production evidence is whatever you measured, and is not fused with the evaluation score.** The score is a test pass rate; an observation is a latency or error rate. They are shown side by side rather than combined into a "prediction accuracy", because asserting one predicted the other would be a causal claim the data does not support. Every observation carries a mandatory `source` and there is no code path that generates one.
- **The reasoning and evaluation backends are stand-ins in this build.** Bedrock model access is granted on this account but every model's quota is `0`, and the adjustment is not self-service, so `BMEM_FAKE_PLANNER` and `BMEM_FAKE_CODEBUILD` are what actually run. The UI says so in the topbar and the readiness panel treats `SIMULATED` as its own state that blocks `demo_ready` — it is not a shade of ready. Every branch-scoped read, fork, merge, revert, snapshot and time-travel query in the demo is real work against the live cluster; what is stood in for is the model call that proposes a strategy and the container that grades it.
- **App Runner, the original deployment target, closed to new customers on 2026-04-30** — this AWS account postdates that. AWS's own successor, ECS Express Mode, needs a pre-built image in ECR rather than supporting source deploy. We ship on Elastic Beanstalk's single-container Docker platform instead, which builds the `Dockerfile` server-side: no local Docker, no ECR, stable for over a decade. See [`infra/deploy_beanstalk.sh`](infra/deploy_beanstalk.sh).

## Running it

```bash
cp .env.example .env      # fill in the CockroachDB connection string and AWS region
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/pytest -m "not cluster and not bedrock"   # offline: validator + pure logic
.venv/bin/pytest                                     # full suite, needs a live cluster
```

Schema lives in [`schema/`](schema/) and applies in filename order; `000_bootstrap.sql` needs an admin connection and must run first. [`infra/preflight.py`](infra/preflight.py) checks a cluster actually supports everything the project needs before you point application code at it.

**The live UI** is a React app in [`web/`](web/) talking to the FastAPI backend over the same REST + SSE endpoints a script would use — the backend has no idea whether a browser or `curl` is on the other end. Two processes in dev:

```bash
BMEM_FAKE_EMBEDDINGS=1 BMEM_FAKE_PLANNER=1 BMEM_FAKE_CODEBUILD=1 \
  .venv/bin/uvicorn bmem.api:app --app-dir src --port 8000    # backend, :8000

cd web && npm install && npm run dev                          # frontend, :5173, proxies /api to :8000
```

then open `http://localhost:5173/?root=<a-branch-id>`. Create and **populate** a trunk branch first — [`demo/script.md`](demo/script.md) has the exact commands. A fresh root is empty, and forks of an empty branch inherit nothing, so the tree renders and the merge works while demonstrating none of the point. Drop the three `BMEM_FAKE_*` flags once Bedrock and CodeBuild are provisioned, and the same UI drives the real thing.

In production there's one process, not two: `npm run build`'s output is what `uvicorn` serves directly at `/` (see the [`Dockerfile`](Dockerfile)'s multi-stage build) — the dev-only split is purely for hot reload.

**The audit surface**, from any MCP client: it's already registered in [`.mcp.json`](.mcp.json) as `bmem-audit`, or run it standalone with `.venv/bin/python -m bmem.mcp_server`.

## Deploying it

```bash
./infra/setup_aws.sh          # S3 bucket + CodeBuild eval project (idempotent)
./infra/deploy_beanstalk.sh   # build + ship the API and UI to a public HTTPS URL (idempotent)
```

Both scripts are safe to re-run — a second `deploy_beanstalk.sh` ships a new version to the existing environment rather than recreating it. See the App Runner note in [Honest limitations](#honest-limitations) for why the target is Elastic Beanstalk.

**Neither has been run against a live environment**, so treat `deploy_beanstalk.sh` as reviewed but unexercised. What *has* been verified is the part it ships: `src/bmem/static` served by uvicorn exactly as the [`Dockerfile`](Dockerfile) arranges it, with `/api` same-origin and no dev proxy. That check is worth running before any deploy — it is what caught the build asking for `/assets/…` while FastAPI served `/static/assets/…`, a 200 followed by a blank page that dev mode cannot reproduce.

## License

MIT — see [LICENSE](LICENSE).
