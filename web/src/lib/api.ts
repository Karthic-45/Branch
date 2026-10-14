import type {
  ApplyResult,
  Config,
  Counterfactual,
  DecisionEvidence,
  DecisionNode,
  Deployment,
  DeploymentEnvironment,
  ClusterStats,
  CopyOnWriteProof,
  CostReport,
  ExecutionManifest,
  MemoryDiff,
  MergeRow,
  IsolationProof,
  ProjectStats,
  ReplayResponse,
  ProductionEvidence,
  ReadinessReport,
  ResilienceProof,
  SecurityReport,
  StrategyComparison,
  TemporalProof,
  BeliefPage,
  ProjectDeployment,
  ArchiveEntry,
  AwsStorage,
  AwsBuilds,
  McpTools,
  BranchVerdict,
} from "./types";

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchConfig(): Promise<Config> {
  return fetch("/api/config").then((r) => asJson<Config>(r));
}

export function fetchMerges(rootId: string): Promise<MergeRow[]> {
  return fetch(`/api/merges/${encodeURIComponent(rootId)}`).then((r) => asJson<MergeRow[]>(r));
}

export function startMigration(parentId: string, goal: string, n: number): Promise<void> {
  return fetch("/api/migrations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: parentId, goal, n }),
  }).then((r) => asJson(r));
}

export async function createRepo(
  repoUrl: string, ref: string | null, hypothesis: string, testSelector: string,
): Promise<string> {
  const res = await fetch("/api/repos", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_url: repoUrl, ref, hypothesis, test_selector: testSelector }),
  });
  const body = await asJson<{ root_id: string }>(res);
  return body.root_id;
}

export function revertMerge(mergeId: string): Promise<void> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/revert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }).then((r) => asJson(r));
}

export function fetchEvidence(mergeId: string): Promise<DecisionEvidence> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/evidence`).then((r) =>
    asJson<DecisionEvidence>(r),
  );
}

export function applyMerge(
  mergeId: string, runValidation: boolean, allowBaseChanged: boolean,
): Promise<ApplyResult> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_validation: runValidation, allow_base_changed: allowBaseChanged }),
  }).then((r) => asJson<ApplyResult>(r));
}

export function prDescriptionUrl(mergeId: string): string {
  return `/api/merges/${encodeURIComponent(mergeId)}/pr-description`;
}

export function fetchManifest(branchId: string): Promise<ExecutionManifest> {
  return fetch(`/api/branches/${encodeURIComponent(branchId)}/manifest`).then((r) =>
    asJson<ExecutionManifest>(r),
  );
}

export function fetchCounterfactual(mergeId: string): Promise<Counterfactual> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/counterfactual`).then((r) =>
    asJson<Counterfactual>(r),
  );
}

export function fetchMemoryDiff(mergeId: string): Promise<MemoryDiff> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/memory-diff`).then((r) =>
    asJson<MemoryDiff>(r),
  );
}

export function fetchDecisionGraph(rootId: string): Promise<DecisionNode[]> {
  return fetch(`/api/decisions/${encodeURIComponent(rootId)}`).then((r) =>
    asJson<DecisionNode[]>(r),
  );
}

export function fetchFailures(parentId: string): Promise<StrategyComparison[]> {
  return fetch(`/api/branches/${encodeURIComponent(parentId)}/failures`).then((r) =>
    asJson<StrategyComparison[]>(r),
  );
}

export function replayBranch(branchId: string): Promise<ReplayResponse> {
  return fetch(`/api/branches/${encodeURIComponent(branchId)}/replay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  }).then((r) => asJson<ReplayResponse>(r));
}

export function fetchProjectStats(rootId: string): Promise<ProjectStats> {
  return fetch(`/api/stats/${encodeURIComponent(rootId)}`).then((r) => asJson<ProjectStats>(r));
}

export function fetchCostReport(rootId: string): Promise<CostReport> {
  return fetch(`/api/cost/${encodeURIComponent(rootId)}`).then((r) => asJson<CostReport>(r));
}

export function fetchClusterStats(): Promise<ClusterStats> {
  return fetch("/api/cluster").then((r) => asJson<ClusterStats>(r));
}

export function fetchSecurityReport(): Promise<SecurityReport> {
  return fetch("/api/security").then((r) => asJson<SecurityReport>(r));
}

export function runIsolationProof(): Promise<IsolationProof> {
  return fetch("/api/proofs/isolation", { method: "POST" }).then((r) => asJson<IsolationProof>(r));
}

export function runCowProof(seedRows: number, forks: number): Promise<CopyOnWriteProof> {
  return fetch(`/api/proofs/copy-on-write?seed_rows=${seedRows}&forks=${forks}`, {
    method: "POST",
  }).then((r) => asJson<CopyOnWriteProof>(r));
}

export function runTemporalProof(): Promise<TemporalProof> {
  return fetch("/api/proofs/temporal", { method: "POST" }).then((r) => asJson<TemporalProof>(r));
}

export function decisionReportUrl(mergeId: string): string {
  return `/api/merges/${encodeURIComponent(mergeId)}/report.pdf`;
}

export function executiveReportUrl(rootId: string): string {
  return `/api/reports/${encodeURIComponent(rootId)}/executive.pdf`;
}

export function engineeringReportUrl(rootId: string): string {
  return `/api/reports/${encodeURIComponent(rootId)}/engineering.pdf`;
}

export function projectArchiveUrl(rootId: string): string {
  return `/api/reports/${encodeURIComponent(rootId)}/archive.zip`;
}

export function runResilienceProof(branches: number, failures: number): Promise<ResilienceProof> {
  return fetch(`/api/proofs/resilience?branches=${branches}&failures=${failures}`, {
    method: "POST",
  }).then((r) => asJson<ResilienceProof>(r));
}

export function fetchDeployments(mergeId: string): Promise<Deployment[]> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/deployments`).then((r) =>
    asJson<Deployment[]>(r),
  );
}

export function fetchEnvironments(mergeId: string): Promise<DeploymentEnvironment[]> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/environments`).then((r) =>
    asJson<DeploymentEnvironment[]>(r),
  );
}

export function createDeployment(
  mergeId: string, environment: string, commitSha: string | null,
  opts: { status?: string; target?: string | null; detail?: string | null } = {},
): Promise<Deployment> {
  return fetch(`/api/merges/${encodeURIComponent(mergeId)}/deployments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      environment,
      commit_sha: commitSha,
      status: opts.status ?? "running",
      target: opts.target ?? null,
      detail: opts.detail ?? null,
    }),
  }).then((r) => asJson<Deployment>(r));
}

export function rollbackDeployment(deploymentId: string, reason: string): Promise<unknown> {
  return fetch(`/api/deployments/${encodeURIComponent(deploymentId)}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  }).then((r) => asJson(r));
}

export function recordObservation(
  deploymentId: string, metric: string, phase: "before" | "after", value: number, source: string,
): Promise<unknown> {
  return fetch(`/api/deployments/${encodeURIComponent(deploymentId)}/observations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ metric, phase, value, source }),
  }).then((r) => asJson(r));
}

export function fetchDeploymentEvidence(deploymentId: string): Promise<ProductionEvidence> {
  return fetch(`/api/deployments/${encodeURIComponent(deploymentId)}/evidence`).then((r) =>
    asJson<ProductionEvidence>(r),
  );
}

export function fetchReadiness(): Promise<ReadinessReport> {
  return fetch("/api/readiness").then((r) => asJson<ReadinessReport>(r));
}

export function fetchBeliefs(
  branchId: string,
  opts: { q?: string; includeTombstoned?: boolean; limit?: number; offset?: number } = {},
): Promise<BeliefPage> {
  const p = new URLSearchParams();
  if (opts.q) p.set("q", opts.q);
  if (opts.includeTombstoned) p.set("include_tombstoned", "true");
  if (opts.limit !== undefined) p.set("limit", String(opts.limit));
  if (opts.offset) p.set("offset", String(opts.offset));
  const qs = p.toString();
  return fetch(`/api/branches/${encodeURIComponent(branchId)}/beliefs${qs ? `?${qs}` : ""}`).then(
    (r) => asJson<BeliefPage>(r),
  );
}

export function fetchProjectDeployments(rootId: string): Promise<ProjectDeployment[]> {
  return fetch(`/api/projects/${encodeURIComponent(rootId)}/deployments`).then((r) =>
    asJson<ProjectDeployment[]>(r),
  );
}

export function fetchArchiveListing(rootId: string): Promise<ArchiveEntry[]> {
  return fetch(`/api/reports/${encodeURIComponent(rootId)}/archive-listing`).then((r) =>
    asJson<ArchiveEntry[]>(r),
  );
}

export function fetchAwsStorage(rootId: string): Promise<AwsStorage> {
  return fetch(`/api/aws/storage/${encodeURIComponent(rootId)}`).then((r) => asJson<AwsStorage>(r));
}

export function fetchAwsBuilds(rootId: string): Promise<AwsBuilds> {
  return fetch(`/api/aws/builds/${encodeURIComponent(rootId)}`).then((r) => asJson<AwsBuilds>(r));
}

/** Plain-text preview of one of the app's own S3 objects (runs/ or applied/). */
export async function fetchAwsObject(key: string): Promise<string> {
  const r = await fetch(`/api/aws/object?key=${encodeURIComponent(key)}`);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || `HTTP ${r.status}`);
  }
  return r.text();
}

export function fetchMcpTools(): Promise<McpTools> {
  return fetch("/api/mcp/tools").then((r) => asJson<McpTools>(r));
}

export function fetchBranchVerdicts(rootId: string): Promise<BranchVerdict[]> {
  return fetch(`/api/branches/${encodeURIComponent(rootId)}/verdicts`).then((r) => asJson<BranchVerdict[]>(r));
}

export function fetchBranchVerdict(branchId: string): Promise<BranchVerdict> {
  return fetch(`/api/branches/${encodeURIComponent(branchId)}/verdict`).then((r) => asJson<BranchVerdict>(r));
}
