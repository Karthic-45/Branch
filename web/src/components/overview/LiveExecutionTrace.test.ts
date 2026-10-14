import { describe, expect, it } from "vitest";
import { computeTrace } from "./LiveExecutionTrace";
import type { FeedEvent, Stage } from "../../lib/types";

let t = 1000;
const ev = (stage: Stage, branch_id: string | null = null, message = ""): FeedEvent => ({
  ts: t++, branch_id, stage, message,
});

const STEP = { ingest: 0, plan: 1, fork: 2, retrieve: 3, reason: 4, upload: 5, build: 6, score: 7, merge: 8 };

function startRun(): FeedEvent[] {
  return [
    ev("started", null, 'Migration requested: "x"'),
    ev("planning", null, "Proposing 2 strategies"),
    ev("planned", null, "Proposed: a, b"),
    ev("forked", "A", "Forked for: strategy A"),
    ev("forked", "B", "Forked for: strategy B"),
  ];
}

describe("computeTrace", () => {
  it("returns null with no events", () => {
    expect(computeTrace([], [])).toBeNull();
  });

  it("shows planning as the live step before any fork", () => {
    const tr = computeTrace([ev("started"), ev("planning", null, "Proposing 4")], [])!;
    expect(tr.states[STEP.plan]).toBe("active");
    expect(tr.states[STEP.ingest]).toBe("skipped"); // migrations start at Plan
    expect(tr.current).toBe(STEP.plan);
    expect(tr.done || tr.failed).toBe(false);
  });

  it("tracks where each branch is while they run in parallel", () => {
    const events = [
      ...startRun(),
      ev("exploring", "A"), ev("explored", "A"), ev("evaluating", "A"),
      ev("building", "A", "CodeBuild: Pre Build"),
      ev("exploring", "B"),
    ];
    const tr = computeTrace(events, [])!;
    expect(tr.states[STEP.plan]).toBe("done");
    expect(tr.states[STEP.fork]).toBe("done");
    expect(tr.states[STEP.retrieve]).toBe("active"); // B is searching memory
    expect(tr.states[STEP.build]).toBe("active");    // A is in CodeBuild
    expect(tr.states[STEP.score]).toBe("pending");
    expect(tr.current).toBe(STEP.build);              // furthest live work
    expect(tr.counts[STEP.build]).toBe(1);
    expect(tr.n).toBe(2);
    expect(tr.branches.map((b) => b.label)).toEqual(["strategy A", "strategy B"]);
  });

  it("marks Score partial while only some branches are scored", () => {
    const events = [
      ...startRun(),
      ev("exploring", "A"), ev("explored", "A"), ev("evaluating", "A"), ev("scored", "A", "91%"),
      ev("exploring", "B"), ev("explored", "B"), ev("evaluating", "B"),
    ];
    const tr = computeTrace(events, [])!;
    expect(tr.states[STEP.score]).toBe("partial");
    expect(tr.states[STEP.upload]).toBe("active");
    expect(tr.current).toBe(STEP.upload);
  });

  it("completes on merge and marks steps the run never used as skipped", () => {
    const events = [
      ...startRun(),
      ev("exploring", "A"), ev("explored", "A"), ev("evaluating", "A"), ev("scored", "A"),
      ev("exploring", "B"), ev("explored", "B"), ev("evaluating", "B"), ev("scored", "B"),
      ev("merging", null, "Promoting a"), ev("merged", "A"), ev("abandoned", "B"),
    ];
    const tr = computeTrace(events, [])!;
    expect(tr.done).toBe(true);
    expect(tr.states[STEP.merge]).toBe("done");
    expect(tr.states[STEP.build]).toBe("skipped"); // fake evaluator: no CodeBuild
    expect(tr.states[STEP.ingest]).toBe("skipped");
  });

  it("fails on the step the run had reached when the error arrived", () => {
    const events = [
      ...startRun(),
      ev("exploring", "A"), ev("explored", "A"), ev("evaluating", "A"), ev("scored", "A"),
      ev("exploring", "B"), ev("explored", "B"), ev("evaluating", "B"), ev("scored", "B"),
      ev("merging", null, "Promoting a"),
      ev("error", null, "Migration failed: already merged"),
    ];
    const tr = computeTrace(events, [])!;
    expect(tr.failed).toBe(true);
    expect(tr.states[STEP.merge]).toBe("failed");
    expect(tr.states[STEP.score]).toBe("done");
    expect(tr.current).toBe(STEP.merge);
  });

  it("only considers the latest run", () => {
    const old = [...startRun(), ev("error", null, "old failure")];
    const fresh = [ev("started"), ev("planning")];
    const tr = computeTrace([...old, ...fresh], [])!;
    expect(tr.failed).toBe(false);
    expect(tr.branches).toHaveLength(0);
    expect(tr.current).toBe(STEP.plan);
  });

  it("mid-run, marks a step every branch went straight past as skipped, not done", () => {
    const events = [
      ...startRun(),
      ev("exploring", "A"), ev("explored", "A"), ev("evaluating", "A"), ev("scored", "A"),
      ev("exploring", "B"), ev("explored", "B"), ev("evaluating", "B"), ev("scored", "B"),
      ev("merging", null, "Promoting a"),
    ];
    const tr = computeTrace(events, [])!;
    expect(tr.done || tr.failed).toBe(false);
    expect(tr.states[STEP.build]).toBe("skipped");
    expect(tr.states[STEP.upload]).toBe("done");
    expect(tr.states[STEP.merge]).toBe("active");
  });
});
