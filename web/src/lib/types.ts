// Mirrors src/bmem/api.py's response shapes exactly. This is the one file
// that has to stay in lockstep with the backend; everything else in the app
// only ever touches these types, never a raw fetch response.

export type BranchStatus = "exploring" | "merged" | "abandoned";

export interface BranchNode {
  id: string;
  parent_id: string | null;
  hypothesis: string | null;
  status: BranchStatus;
  score: number | null;
  depth: number;
  /** When the branch was forked (ISO 8601); null from older servers. */
  created_at?: string | null;
  reason?: string | null;
  passed?: number | null;
  total?: number | null;
}

// Fine-grained stages, in the order a single branch actually passes through
// them. Wider than BranchStatus on purpose -- status alone can't distinguish
// "forked and about to explore" from "CodeBuild is 90% through the suite".
export type Stage =
  // Repository ingest, emitted by POST /api/repos before any migration runs.
  // These were missing here while the backend emitted them, so the ingest
  // phase silently fell through every stage switch -- caught by the
  // production build, not by tsc --noEmit, because casts had masked it.
  | "ingesting"
  | "ingested"
  | "started"
  | "planning"
  | "planned"
  | "forked"
  | "exploring"
  | "explored"
  | "evaluating"
  | "uploading"
  | "building"
  | "scored"
  | "merging"
  | "merged"
  | "abandoned"
  | "reverted"
  | "error";

export interface FeedEvent {
  ts: number;
  branch_id: string | null;
  stage: Stage;
  message: string;
}

export interface Config {
  fake: Record<string, boolean>;
}

export interface MergeRow {
  merge_id: string;
  winner_id: string;
  winner_hypothesis: string | null;
  winner_score: number | null;
  merged_at: string | null;
  reverted_at: string | null;
  has_patch: boolean;
}

export type ConnState = "idle" | "connecting" | "live" | "disconnected";

export interface ValidationProfile {
  install: string | null;
  lint: string | null;
  typecheck: string | null;
  build: string | null;
  test: string | null;
}

export interface ValidationStep {
  name: string;
  command: string;
  status: "passed" | "failed" | "error";
  output: string;
}

export type ValidationStatus =
  | "base_changed"
  | "patch_failed"
  | "skipped"
  | "passed"
  | "failed";

export interface ApplyResult {
  merge_id: string;
  base_sha: string | null;
  current_head_sha: string | null;
  base_changed: boolean;
  branch: string | null;
  patch_applied: boolean;
  conflicts: string[];
  patch_failure: "malformed" | "conflict" | "unknown" | null;
  conflicting_files: string[];
  validation_status: ValidationStatus;
  validation_steps: ValidationStep[];
  commit_sha: string | null;
  applied_tree_s3_uri: string | null;
}

export interface AlternativeStrategy {
  branch_id: string;
  hypothesis: string | null;
  status: BranchStatus;
  score: number | null;
  belief: string | null;
}

export interface LatestApplication {
  validation_status: ValidationStatus;
  branch: string | null;
  commit_sha: string | null;
  conflicts: string[];
  created_at: string | null;
}

export interface PatchStats {
  files_changed: number;
  files: string[];
  lines_added: number;
  lines_removed: number;
  files_created: number;
  files_deleted: number;
  touches_dependencies: boolean;
  touches_schema: boolean;
  touches_tests: boolean;
  risk: "none" | "low" | "moderate" | "high";
  risk_basis: string;
}

export interface StrategyComparison {
  branch_id: string;
  strategy_name: string | null;
  hypothesis: string | null;
  outcome: "won" | "lost";
  score: number | null;
  passed: number | null;
  total: number | null;
  reason: string | null;
  score_gap: number | null;
  patch_stats: PatchStats | null;
  points: string[];
}

export interface SimilarityPair {
  a: string;
  b: string;
  cosine: number;
}

export interface Counterfactual {
  merge_id: string;
  rationale: string;
  tie_detected: boolean;
  discriminator: string | null;
  margin: number | null;
  winner: StrategyComparison | null;
  alternatives: StrategyComparison[];
  strategy_similarity: SimilarityPair[];
}

export interface DecisionNode {
  merge_id: string;
  run_id: string | null;
  parent_id: string;
  winner_branch_id: string;
  winner_hypothesis: string | null;
  winner_score: number | null;
  merged_at: string | null;
  reverted_at: string | null;
  tie_detected: boolean;
  rationale: string | null;
  alternatives: StrategyComparison[];
  applied_commit_sha: string | null;
  applied_validation_status: ValidationStatus | null;
}

export interface ProjectStats {
  root_id: string;
  source_repo: string | null;
  source_ref: string | null;
  branches: number;
  branches_exploring: number;
  branches_merged: number;
  branches_abandoned: number;
  decisions: number;
  decisions_reverted: number;
  decisions_tie_broken: number;
  strategies_evaluated: number;
  win_rate: number | null;
  average_score: number | null;
  best_score: number | null;
  patch_available_rate: number | null;
  applies_attempted: number;
  applies_validated: number;
  replays: number;
  memory_rows: number;
}

export interface CostLine {
  resource: string;
  usage: string;
  measured: boolean;
  estimated_usd: number | null;
  note: string;
}

export interface CostReport {
  root_id: string;
  lines: CostLine[];
  total_estimated_usd: number | null;
  pricing_configured: boolean;
}

export interface ClusterStats {
  branches_total: number;
  memory_total: number;
  merges_total: number;
  manifests_total: number;
  outcomes_total: number;
  applications_total: number;
  active_queries: number | null;
  database: string | null;
  vector_index_present: boolean | null;
  gc_ttl_seconds: number | null;
  note: string;
}

export interface SecurityCheck {
  name: string;
  status: "pass" | "warn" | "unavailable";
  detail: string;
  verified_live: boolean;
}

export interface SecurityReport {
  checks: SecurityCheck[];
  codebuild_env_vars: string[];
  note: string;
}

export interface MemoryDelta {
  key: string;
  change: "added" | "changed" | "unchanged" | "tombstoned";
  before: string | null;
  after: string | null;
}

export interface MemoryDiff {
  merge_id: string;
  parent_id: string;
  winner_id: string;
  parent_before_count: number;
  branch_count: number;
  parent_after_count: number;
  added: MemoryDelta[];
  changed: MemoryDelta[];
  tombstoned: MemoryDelta[];
  unchanged_count: number;
}

export interface RetrievedMemoryRef {
  key: string;
  source_branch_id: string;
  inherited: boolean;
  distance: number;
}

export interface ExecutionManifest {
  branch_id: string;
  run_id: string;
  strategy_name: string;
  hypothesis: string | null;
  reasoning_provider: string | null;
  reasoning_model_id: string | null;
  explore_prompt_version: string | null;
  embedding_provider: string | null;
  embedding_model_id: string | null;
  retrieved_memories: RetrievedMemoryRef[];
  target_repo: string | null;
  target_ref: string | null;
  base_sha: string | null;
  test_selector: string | null;
  codebuild_project: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ReplayResponse {
  original_branch_id: string;
  original_score: number | null;
  replay_branch_id: string;
  replay_run_id: string;
  replay_score: number;
  replay_reason: string;
  score_delta: number | null;
}

export interface DecisionEvidence {
  merge_id: string;
  parent_id: string;
  winner_id: string;
  winning_hypothesis: string | null;
  winning_score: number | null;
  belief: string | null;
  merged_at: string | null;
  reverted_at: string | null;
  patch_available: boolean;
  base_sha: string | null;
  alternatives: AlternativeStrategy[];
  latest_application: LatestApplication | null;
}

export interface ProofStep {
  step: string;
  detail: string;
  elapsed_ms: number;
}

export interface IsolationProof {
  verdict: "verified" | "failed";
  headline: string;
  branch_a: string;
  branch_b: string;
  secret: string;
  hits_from_a: number;
  hits_from_b: number;
  steps: ProofStep[];
}

export interface CopyOnWriteProof {
  verdict: "verified" | "failed";
  headline: string;
  parent_rows: number;
  forks_created: number;
  memory_rows_added_by_forking: number;
  fork_latency_ms: number[];
  steps: ProofStep[];
}

export interface TemporalSnapshot {
  label: string;
  keys: string[];
  count: number;
  source: string;
}

export interface TemporalProof {
  verdict: "verified" | "failed";
  headline: string;
  merge_id: string;
  before_merge: TemporalSnapshot;
  after_merge: TemporalSnapshot;
  steps: ProofStep[];
}

export interface ResilienceOutcome {
  branch_id: string;
  strategy: string;
  injected_failure: string | null;
  survived: boolean;
  detail: string;
}

export interface ResilienceProof {
  verdict: "verified" | "failed";
  headline: string;
  branches_started: number;
  failures_injected: number;
  branches_completed: number;
  tree_consistent: boolean;
  audit_consistent: boolean;
  outcomes: ResilienceOutcome[];
  steps: ProofStep[];
}

export interface Deployment {
  id: string;
  merge_id: string;
  commit_sha: string | null;
  environment: string;
  status: "pending" | "running" | "healthy" | "failed" | "rolled_back";
  target: string | null;
  artifact: string | null;
  detail: string | null;
  started_at: string | null;
  completed_at: string | null;
  rolled_back_at: string | null;
  superseded_by: string | null;
}

export interface DeploymentEnvironment {
  name: string;
  reached: boolean;
  latest_status: string | null;
  latest_deployment_id: string | null;
}

export interface Observation {
  id: string;
  metric: string;
  phase: "before" | "after";
  value: number;
  unit: string | null;
  source: string;
  note: string | null;
  observed_at: string | null;
}

export interface MetricComparison {
  metric: string;
  unit: string | null;
  before: number | null;
  after: number | null;
  delta: number | null;
  percent_change: number | null;
  sources: string[];
}

export interface ProductionEvidence {
  deployment_id: string;
  merge_id: string;
  evaluation_score: number | null;
  observations: Observation[];
  comparisons: MetricComparison[];
  note: string;
}

export interface ComponentCheck {
  name: string;
  status: "ready" | "degraded" | "not_ready" | "simulated";
  detail: string;
  remediation: string | null;
  latency_ms: number | null;
  probe: string;
}

export interface ReadinessReport {
  overall: "ready" | "degraded" | "not_ready" | "simulated";
  demo_ready: boolean;
  checks: ComponentCheck[];
  note: string;
}

export interface Belief {
  key: string;
  content: string | null;
  tombstone: boolean;
  created_at_hlc: string;
  s3_uri: string | null;
  origin_branch_id: string;
  origin_hypothesis: string | null;
  origin_depth: number;
  inherited: boolean;
  shadowed_by: string | null;
}

export interface LineageEntry {
  branch_id: string;
  hypothesis: string | null;
  depth: number;
  horizon: string | null;
}

export interface BeliefPage {
  branch_id: string;
  beliefs: Belief[];
  total_visible: number;
  own: number;
  inherited: number;
  tombstoned: number;
  shadowed: number;
  lineage: LineageEntry[];
  truncated: boolean;
}

export interface ProjectDeployment {
  deployment: Deployment;
  winner_hypothesis: string | null;
  winner_score: number | null;
  merged_at: string | null;
}

export interface ArchiveEntry {
  path: string;
  bytes: number;
}

// ── Live AWS inventory (src/bmem/aws_inventory.py) ──

export interface S3Object {
  key: string;
  name: string;
  size: number;
  last_modified: string | null;
  storage_class: string | null;
}

export interface AwsBranch {
  branch_id: string;
  strategy: string | null;
  hypothesis: string | null;
  status: BranchStatus;
  score: number | null;
  outcome: "won" | "lost" | null;
  passed: number | null;
  total: number | null;
  reason: string | null;
  objects: S3Object[];
}

export interface AwsRun {
  run_id: string;
  started_at: string | null;
  parent_id: string | null;
  reasoning_model: string | null;
  embedding_model: string | null;
  codebuild_project: string | null;
  target_repo: string | null;
  base_sha: string | null;
  branches: AwsBranch[];
}

export interface AwsMerge {
  merge_id: string;
  parent_id: string;
  winner_id: string;
  merged_at: string | null;
  reverted_at: string | null;
}

export interface AwsStorage {
  bucket: string | null;
  region: string;
  runs: AwsRun[];
  applied: (S3Object & { merge_id: string })[];
  merges?: AwsMerge[];
  totals: { objects: number; bytes: number };
  error: string | null;
}

export interface AwsBuildPhase {
  phase: string | null;
  status: string | null;
  seconds: number | null;
}

export interface AwsBuild {
  id: string;
  build_number: number | null;
  status: string | null;
  current_phase: string | null;
  run_id: string | null;
  branch_id: string | null;
  strategy: string | null;
  start_time: string | null;
  end_time: string | null;
  phases: AwsBuildPhase[];
  logs_url: string | null;
  artifact_s3: string | null;
}

export interface AwsBuilds {
  project: string | null;
  region: string;
  builds: AwsBuild[];
  error: string | null;
}

export interface McpTool {
  name: string;
  description: string;
  read_only: boolean | null;
  destructive: boolean | null;
}

export interface McpTools {
  server: string;
  tools: McpTool[];
  total: number;
  read_only: number;
  may_write: number;
}

/** Why a branch was merged or abandoned, from /api/branches/{root}/verdicts. */
export interface BranchVerdict {
  branch_id: string;
  parent_id: string | null;
  strategy: string | null;
  hypothesis: string | null;
  status: string;
  kind:
    | "merged" | "reverted" | "lost_on_score" | "lost_tie_break" | "lost_inert"
    | "declined" | "failed"
    | "won_not_merged" | "abandoned_unscored" | "in_progress";
  decision: "merged" | "abandoned" | "pending";
  headline: string;
  reasons: string[];
  score: number | null;
  passed: number | null;
  total: number | null;
  evaluation_reason: string | null;
  run_id: string | null;
  merge_id: string | null;
  winner_branch_id: string | null;
  winner_strategy: string | null;
}
