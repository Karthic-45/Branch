import type { FeedEvent, Stage } from "./types";

/** The five real systems a run touches. Drawn as boxes in SystemFlow. */
export type NodeId = "db" | "cp" | "bedrock" | "s3" | "codebuild";

/** Directed links between them. Every one is a real call the backend makes --
 * nothing here is drawn for decoration. */
export type EdgeId = "db-cp" | "cp-db" | "cp-bedrock" | "cp-s3" | "s3-cb" | "cb-cp";

export interface FlowState {
  edges: Set<EdgeId>;
  nodes: Set<NodeId>;
  /** Plain-language "what is happening right now", for the caption under the
   * diagram. The whole point of the panel is that a viewer never has to guess. */
  caption: string;
  tone: "idle" | "reasoning" | "build" | "success" | "danger";
}

const IDLE: FlowState = {
  edges: new Set(),
  nodes: new Set(["db", "cp"]),
  caption: "Idle — waiting for a migration to start.",
  tone: "idle",
};

/** Which edges light up for a given in-flight stage. A branch sitting in
 * "building" means S3 is feeding CodeBuild right now; a branch in "exploring"
 * means the control plane is reading inherited memory out of CockroachDB and
 * asking Bedrock to reason over it.
 *
 * Deliberately excludes "merged" (and "abandoned"/"reverted"/"error"): those
 * are resting *outcomes*, not ongoing activity. branchStage never deletes a
 * branch's entry once it finishes -- it just keeps mapping that branch's id
 * to its final stage forever -- so if a terminal stage were listed here, the
 * edge it names would stay lit indefinitely after the run actually ended.
 * "merging" (the single brief run-level action right before the decision)
 * is what should flash on merge; "merged" is what comes after it settles. */
const STAGE_EDGES: Partial<Record<Stage, EdgeId[]>> = {
  planning: ["db-cp", "cp-bedrock"],
  planned: ["cp-bedrock"],
  forked: ["cp-db"],
  exploring: ["db-cp", "cp-bedrock"],
  explored: ["cp-db"],
  evaluating: ["cp-s3"],
  uploading: ["cp-s3"],
  building: ["s3-cb"],
  scored: ["cb-cp"],
  merging: ["cp-db"],
};

const EDGE_NODES: Record<EdgeId, NodeId[]> = {
  "db-cp": ["db", "cp"],
  "cp-db": ["cp", "db"],
  "cp-bedrock": ["cp", "bedrock"],
  "cp-s3": ["cp", "s3"],
  "s3-cb": ["s3", "codebuild"],
  "cb-cp": ["codebuild", "cp"],
};

function captionFor(stage: Stage, message: string): { caption: string; tone: FlowState["tone"] } {
  switch (stage) {
    case "planning":
      return { caption: "Claude is reading the trunk's memory and proposing migration strategies.", tone: "reasoning" };
    case "planned":
      return { caption: "Strategies proposed. Preparing to fork one branch per hypothesis.", tone: "reasoning" };
    case "forked":
      return { caption: "Forking memory — one row per branch, no data copied.", tone: "reasoning" };
    case "exploring":
      return { caption: "Each branch is searching its own inherited memory, in isolation.", tone: "reasoning" };
    case "explored":
      return { caption: "A branch reached a conclusion and wrote it back to its own lineage.", tone: "reasoning" };
    case "evaluating":
    case "uploading":
      return { caption: "Uploading candidate patches to S3 for sandboxed evaluation.", tone: "build" };
    case "building":
      // The message carries the real CodeBuild phase ("CodeBuild: Pre Build").
      return { caption: message.startsWith("CodeBuild") ? `${message} — running the real test suite.` : "CodeBuild is running the real test suite.", tone: "build" };
    case "scored":
      return { caption: "Test results are back. Scores written to CockroachDB.", tone: "build" };
    case "merging":
      return { caption: "Promoting the highest-scoring branch — one serializable transaction.", tone: "success" };
    case "merged":
      return { caption: "Winner merged into the trunk. Its belief is now the shared one.", tone: "success" };
    case "abandoned":
      return { caption: "Losing branches abandoned — their conclusions are no longer retrievable.", tone: "idle" };
    case "reverted":
      return { caption: "Merge reverted. The trunk is back to exactly what it believed before.", tone: "idle" };
    case "error":
      return { caption: message, tone: "danger" };
    case "started":
      return { caption: "Migration requested. Spinning up the run.", tone: "reasoning" };
    default:
      return { caption: message, tone: "idle" };
  }
}

/** Reads the same event stream the feed and tree read -- one source of truth,
 * three views of it, rather than a separate run-state model to keep in sync. */
export function deriveFlow(events: FeedEvent[], branchStage: Map<string, Stage>): FlowState {
  if (!events.length) return IDLE;

  const edges = new Set<EdgeId>();
  const nodes = new Set<NodeId>(["db", "cp"]);

  // Any branch currently sitting in a stage lights that stage's path. Several
  // branches run in parallel, so more than one path is legitimately hot at once.
  for (const stage of branchStage.values()) {
    for (const edge of STAGE_EDGES[stage] ?? []) edges.add(edge);
  }

  const last = events[events.length - 1];
  // Run-level events (planning, merging) carry no branch_id, so they'd be
  // missed by the loop above -- fold the newest one in explicitly.
  for (const edge of STAGE_EDGES[last.stage] ?? []) edges.add(edge);

  for (const edge of edges) {
    for (const node of EDGE_NODES[edge]) nodes.add(node);
  }

  const { caption, tone } = captionFor(last.stage, last.message);
  return { edges, nodes, caption, tone };
}
