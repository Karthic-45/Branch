import type { Stage } from "./types";

export const pct = (v: number | null | undefined): number | null =>
  v === null || v === undefined ? null : Math.round(v * 100);

export const truncate = (s: string, n: number): string =>
  s.length <= n ? s : s.slice(0, n - 1) + "…";

/** Five steps every branch visibly passes through, shown by the tree, the
 * stepper cards, and the top-level StageRail alike -- one shared vocabulary
 * rather than three components independently deciding what "in progress"
 * means. */
export const STEPS = ["forked", "exploring", "evaluating", "scored", "resolved"] as const;
export type Step = (typeof STEPS)[number];

export const STEP_LABEL: Record<Step, string> = {
  forked: "Fork",
  exploring: "Explore",
  evaluating: "Evaluate",
  scored: "Score",
  resolved: "Merge",
};

const STAGE_TO_STEP: Partial<Record<Stage, Step>> = {
  forked: "forked",
  exploring: "exploring",
  explored: "exploring",
  evaluating: "evaluating",
  uploading: "evaluating",
  building: "evaluating",
  scored: "scored",
  merged: "resolved",
  abandoned: "resolved",
};

export function stepFor(stage: Stage | undefined): Step {
  if (!stage) return "forked";
  return STAGE_TO_STEP[stage] ?? "forked";
}

export const STAGE_LABEL: Record<Stage, string> = {
  ingesting: "Ingesting",
  ingested: "Ingested",
  started: "Started",
  planning: "Planning",
  planned: "Planned",
  forked: "Forked",
  exploring: "Exploring",
  explored: "Explored",
  evaluating: "Evaluating",
  uploading: "Uploading",
  building: "Building",
  scored: "Scored",
  merging: "Merging",
  merged: "Merged",
  abandoned: "Abandoned",
  reverted: "Reverted",
  error: "Error",
};

/** Four meanings, four hues, used identically everywhere in the app:
 * reasoning (violet) while an LLM is thinking, build (amber) while
 * CodeBuild/AWS is doing something, success (emerald) once resolved well,
 * danger (rose) for abandoned/error. "neutral" covers everything else --
 * bookkeeping stages with no activity of their own to depict. */
export type StageCategory = "neutral" | "reasoning" | "build" | "success" | "danger";

const STAGE_CATEGORY: Record<Stage, StageCategory> = {
  // Ingest is real work against the repository and the embedding model, so
  // it reads as build activity rather than bookkeeping.
  ingesting: "build",
  ingested: "success",
  started: "neutral",
  planning: "reasoning",
  planned: "neutral",
  forked: "neutral",
  exploring: "reasoning",
  explored: "neutral",
  evaluating: "build",
  uploading: "build",
  building: "build",
  scored: "neutral",
  merging: "neutral",
  merged: "success",
  abandoned: "neutral",
  reverted: "neutral",
  error: "danger",
};

export function categoryFor(stage: Stage): StageCategory {
  return STAGE_CATEGORY[stage];
}

export const IN_FLIGHT_STAGES: ReadonlySet<Stage> = new Set([
  "exploring",
  "evaluating",
  "uploading",
  "building",
  "planning",
  "merging",
]);

/** CodeBuild phase events are formatted as "CodeBuild: Pre Build" by
 * evaluate.py's _emit -- pulled back apart here so the UI can drive a
 * phase-specific progress indicator instead of just showing the raw string. */
const CODEBUILD_PHASE = /^CodeBuild: (.+)$/;

export function codeBuildPhase(message: string): string | null {
  return CODEBUILD_PHASE.exec(message)?.[1] ?? null;
}

export const CODEBUILD_PHASES = [
  "Submitted",
  "Provisioning",
  "Download Source",
  "Install",
  "Pre Build",
  "Build",
  "Post Build",
  "Completed",
] as const;
