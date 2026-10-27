import { useEffect, useMemo, useState } from "react";
import {
  Rocket, GitMerge, ShieldCheck, Upload, ClipboardList, Plus, X, Loader2, AlertTriangle,
} from "lucide-react";
import type { DecisionNode, ProductionEvidence, ProjectDeployment } from "../../lib/types";
import { createDeployment, fetchCounterfactual, fetchDeploymentEvidence, rollbackDeployment } from "../../lib/api";
import { Ring } from "../common/Motion";
import { PageSkeleton, Shimmer } from "../common/Loading";

/* What this page is for
   ---------------------
   Branchable Memory decides *which* change should ship; it never ships it.
   This page is the ledger for the step after the decision: you deploy the
   validated commit with your own tooling (infra/deploy_beanstalk.sh, a CI
   pipeline, ...), record it here, and then everyone can answer "which
   decision is live in production?" and roll back with a stated reason.
   Every number below is read from the decisions and deployments tables. */

interface Props {
  rootId: string | null;
  rows: ProjectDeployment[];
  decisions: DecisionNode[];
  onOpenDecision: (mergeId: string) => void;
  onToast?: (text: string, tone?: "success" | "error" | "neutral") => void;
  /** Re-read deployments from the server after this page changes one. */
  onChanged?: () => void;
  /** Deployments or decisions are still being fetched. */
  loading?: boolean;
}

const ENVIRONMENTS = ["development", "staging", "production"] as const;
const RECORD_STATUSES = ["running", "healthy", "failed", "pending"] as const;

const STATUS_STYLE: Record<string, string> = {
  healthy: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400",
  failed: "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-400",
  rolled_back: "bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-400",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-400",
  pending: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};

const CARD = "rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] shadow-xs";

export function DeploymentsPage({
  rootId, rows, decisions, onOpenDecision, onToast, onChanged, loading = false,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const live = decisions.filter((d) => !d.reverted_at);
  const validated = live.filter((d) => d.applied_validation_status === "passed" && d.applied_commit_sha);

  // Latest deployment per environment -- "what is where right now".
  const byEnv = useMemo(() => {
    const m = new Map<string, ProjectDeployment>();
    for (const r of rows) {
      const cur = m.get(r.deployment.environment);
      const t = Date.parse(r.deployment.started_at ?? "") || 0;
      if (!cur || t > (Date.parse(cur.deployment.started_at ?? "") || 0)) {
        m.set(r.deployment.environment, r);
      }
    }
    return m;
  }, [rows]);

  if (loading && rows.length === 0 && decisions.length === 0) {
    return <PageSkeleton variant="table" caption="Reading decisions and deployment records from CockroachDB…" />;
  }

  const active = rows.find((r) => r.deployment.id === selectedId) ?? rows[0] ?? null;
  const rolledBack = rows.filter((r) => r.deployment.status === "rolled_back").length;
  const healthy = rows.filter((r) => r.deployment.status === "healthy").length;

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white">Deployments</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Where each decision has been shipped, and what to roll back to. Recorded by you; this app never deploys on its own.
          </p>
        </div>
        <button
          onClick={() => setFormOpen((o) => !o)}
          disabled={!rootId || live.length === 0}
          title={live.length === 0 ? "Merge a decision first — only a decision can be deployed" : undefined}
          className="flex items-center gap-1.5 self-start rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 sm:self-center"
        >
          {formOpen ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
          {formOpen ? "Close" : "Record a deployment"}
        </button>
      </div>

      {formOpen && (
        <RecordForm
          decisions={live}
          onCancel={() => setFormOpen(false)}
          onDone={(msg) => { setFormOpen(false); onToast?.(msg, "success"); onChanged?.(); }}
          onError={(msg) => onToast?.(msg, "error")}
        />
      )}

      {/* How this is used: the path from decision to production, with real counts */}
      <section className={`${CARD} p-5`}>
        <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">How a decision reaches production</h2>
        <p className="mt-0.5 mb-4 text-[11.5px] text-slate-500">
          The counts are this project's own records. Step 3 happens outside this app, which is why step 4 exists.
        </p>
        <ol className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <Step
            n={1} icon={GitMerge} title="Decision merged"
            count={`${live.length}`} countLabel={live.length === 1 ? "decision in effect" : "decisions in effect"}
            text="A run picks the winning strategy and merges it. Its patch is stored in S3."
          />
          <Step
            n={2} icon={ShieldCheck} title="Applied & validated"
            count={`${validated.length}`} countLabel="with a validated commit"
            text="Decisions → Apply & Validate applies the patch to a fresh checkout, runs the tests and makes a git commit."
          />
          <Step
            n={3} icon={Upload} title="You deploy it"
            count="—" countLabel="done with your own tooling"
            text="Ship that commit with infra/deploy_beanstalk.sh or your CI. Nothing here deploys automatically."
          />
          <Step
            n={4} icon={ClipboardList} title="Record it here"
            count={`${rows.length}`} countLabel={`recorded · ${healthy} healthy · ${rolledBack} rolled back`}
            text="Record environment, commit and status so “what is live?” has an answer. Roll back with a reason."
          />
        </ol>
      </section>

      {/* What is live where */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {ENVIRONMENTS.map((env) => {
          const r = byEnv.get(env);
          return (
            <div key={env} className={`${CARD} p-4`}>
              <div className="flex items-center justify-between">
                <span className="text-[10.5px] font-bold uppercase tracking-wider text-slate-400">{env}</span>
                {r && (
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${STATUS_STYLE[r.deployment.status] ?? STATUS_STYLE.pending}`}>
                    {r.deployment.status.replace("_", " ")}
                  </span>
                )}
              </div>
              {r ? (
                <>
                  <button
                    onClick={() => onOpenDecision(r.deployment.merge_id)}
                    className="mt-1.5 block w-full truncate text-left text-[13px] font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
                    title={r.winner_hypothesis ?? undefined}
                  >
                    {r.winner_hypothesis || `Decision ${r.deployment.merge_id.slice(0, 8)}`}
                  </button>
                  <div className="mt-1 font-mono text-[10.5px] text-slate-500">
                    {r.deployment.commit_sha ? r.deployment.commit_sha.slice(0, 10) : "commit not recorded"}
                    {" · "}
                    {r.deployment.started_at ? new Date(r.deployment.started_at).toLocaleString() : "—"}
                  </div>
                </>
              ) : (
                <p className="mt-1.5 text-[12px] text-slate-400">Nothing recorded for {env}.</p>
              )}
            </div>
          );
        })}
      </section>

      {/* History */}
      <section className={`${CARD} p-6 space-y-5`}>
        <div className="flex items-center justify-between border-b border-slate-100 pb-3 dark:border-slate-800">
          <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Deployment history</h2>
          <span className="rounded-full bg-slate-100 dark:bg-slate-800 px-3 py-1 text-xs font-semibold text-slate-600 dark:text-slate-400">
            {rows.length} {rows.length === 1 ? "deployment" : "deployments"}
          </span>
        </div>

        {rows.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 p-10 text-center dark:border-slate-800">
            <Rocket className="mx-auto mb-3 h-9 w-9 text-slate-300 dark:text-slate-600" />
            <h3 className="text-sm font-bold text-slate-800 dark:text-slate-200">No deployments recorded</h3>
            <p className="mx-auto mt-1 max-w-md text-xs text-slate-400">
              {live.length === 0
                ? "There is no merged decision to deploy yet. Run a migration first."
                : validated.length === 0
                  ? "Validate a decision first (Decisions → Apply & Validate), deploy its commit, then record it here."
                  : "You have a validated commit. Deploy it with your own tooling, then use “Record a deployment”."}
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto rounded-xl border border-slate-100 dark:border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 dark:bg-slate-900/60 text-slate-400 font-semibold border-b border-slate-100 dark:border-slate-800">
                  <tr>
                    <th className="py-2.5 px-3">ID</th>
                    <th className="py-2.5 px-3">Decision</th>
                    <th className="py-2.5 px-3">Environment</th>
                    <th className="py-2.5 px-3">Status</th>
                    <th className="py-2.5 px-3">Score</th>
                    <th className="py-2.5 px-3">Started</th>
                    <th className="py-2.5 px-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800 font-medium">
                  {rows.map((r) => {
                    const d = r.deployment;
                    const canRollback = d.status === "healthy" || d.status === "running";
                    return (
                      <tr
                        key={d.id}
                        onClick={() => setSelectedId(d.id)}
                        className={`cursor-pointer transition-colors hover:bg-slate-50/80 dark:hover:bg-slate-800/40 ${active?.deployment.id === d.id ? "bg-indigo-50/40 dark:bg-indigo-950/20" : ""}`}
                      >
                        <td className="py-2.5 px-3 font-mono font-bold text-slate-800 dark:text-slate-200">{d.id.slice(0, 8)}</td>
                        <td className="max-w-xs truncate py-2.5 px-3 text-indigo-600 dark:text-indigo-400">{r.winner_hypothesis || d.merge_id.slice(0, 8)}</td>
                        <td className="py-2.5 px-3 text-slate-600 dark:text-slate-300">{d.environment}</td>
                        <td className="py-2.5 px-3">
                          <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${STATUS_STYLE[d.status] ?? STATUS_STYLE.pending}`}>
                            {d.status.replace("_", " ").toUpperCase()}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 font-mono text-slate-600 dark:text-slate-300">
                          {r.winner_score !== null ? `${Math.round(r.winner_score * 100)}%` : "—"}
                        </td>
                        <td className="py-2.5 px-3 text-[11px] text-slate-400">{d.started_at ? new Date(d.started_at).toLocaleString() : "—"}</td>
                        <td className="space-x-2 py-2.5 px-3 text-right">
                          {canRollback && (
                            <button
                              onClick={async (e) => {
                                e.stopPropagation();
                                setRollingBack(d.id);
                                try {
                                  await rollbackDeployment(d.id, "User requested rollback from dashboard");
                                  onToast?.(`Deployment ${d.id.slice(0, 8)} marked rolled back`, "success");
                                  onChanged?.();
                                } catch (err) {
                                  onToast?.(err instanceof Error ? err.message : "Rollback failed", "error");
                                } finally {
                                  setRollingBack(null);
                                }
                              }}
                              disabled={rollingBack === d.id}
                              className="cursor-pointer text-[11px] font-semibold text-rose-600 hover:underline"
                            >
                              {rollingBack === d.id ? "Rolling back…" : "Rollback"}
                            </button>
                          )}
                          <button
                            onClick={(e) => { e.stopPropagation(); onOpenDecision(d.merge_id); }}
                            className="cursor-pointer text-[11px] font-semibold text-indigo-600 hover:underline"
                          >
                            View decision
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {active && (
              <div className="space-y-4 rounded-xl border border-slate-200 bg-slate-50/60 p-5 dark:border-slate-800 dark:bg-slate-900/40">
                <div className="flex items-center justify-between border-b border-slate-200/80 pb-3 dark:border-slate-800">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">Deployment {active.deployment.id.slice(0, 8)}</h3>
                  <span className="font-mono text-[11px] text-slate-400">Decision {active.deployment.merge_id.slice(0, 8)}</span>
                </div>
                <div className="grid grid-cols-2 gap-4 text-xs sm:grid-cols-4">
                  <Field k="Environment" v={active.deployment.environment} />
                  <Field k="Status" v={active.deployment.status.replace("_", " ")} />
                  <Field k="Target" v={active.deployment.target ?? "Not recorded"} />
                  <Field k="Commit" v={active.deployment.commit_sha ?? "Not recorded"} mono />
                  <Field k="Started" v={active.deployment.started_at ? new Date(active.deployment.started_at).toLocaleString() : "—"} />
                  <Field k="Completed" v={active.deployment.completed_at ? new Date(active.deployment.completed_at).toLocaleString() : "—"} />
                  <Field k="Rolled back" v={active.deployment.rolled_back_at ? new Date(active.deployment.rolled_back_at).toLocaleString() : "—"} />
                  <Field k="Evaluation score" v={active.winner_score !== null ? `${Math.round(active.winner_score * 100)}%` : "—"} />
                </div>
                {active.deployment.detail && (
                  <p className="rounded-lg border border-slate-100 bg-white p-2.5 font-mono text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
                    {active.deployment.detail}
                  </p>
                )}
                <DeploymentEvidence key={active.deployment.id} deploymentId={active.deployment.id} mergeId={active.deployment.merge_id} />
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/** Test results of the deployed decision, and before/after measurements
 *  recorded around this deployment. Both read from the backend; neither is
 *  shown if it was never recorded. */
function DeploymentEvidence({ deploymentId, mergeId }: { deploymentId: string; mergeId: string }) {
  const [tests, setTests] = useState<{ passed: number; total: number } | null | undefined>(undefined);
  const [evidence, setEvidence] = useState<ProductionEvidence | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    fetchCounterfactual(mergeId)
      .then((c) => { if (!cancelled) setTests(c.winner?.total ? { passed: c.winner.passed ?? 0, total: c.winner.total } : null); })
      .catch(() => { if (!cancelled) setTests(null); });
    fetchDeploymentEvidence(deploymentId)
      .then((e) => { if (!cancelled) setEvidence(e); })
      .catch(() => { if (!cancelled) setEvidence(null); });
    return () => { cancelled = true; };
  }, [deploymentId, mergeId]);

  const comps = evidence?.comparisons ?? [];
  return (
    <div className="grid grid-cols-1 gap-4 border-t border-slate-200/80 pt-4 md:grid-cols-[240px_1fr] dark:border-slate-800">
      <div>
        <h4 className="mb-3 text-[10.5px] font-bold uppercase tracking-wider text-slate-500">Test results</h4>
        {tests === undefined ? (
          <Shimmer w={110} h={110} round={55} />
        ) : tests === null ? (
          <p className="text-xs text-slate-400">No test result recorded for this decision.</p>
        ) : (
          <div className="flex items-center gap-4">
            <Ring pct={tests.passed / tests.total} size={104} stroke={10} color={tests.passed === tests.total ? "#10b981" : "#f59e0b"}>
              <span className="text-lg font-extrabold text-slate-900 dark:text-white">{((tests.passed / tests.total) * 100).toFixed(1)}%</span>
              <span className="text-[9px] font-semibold uppercase text-slate-400">passed</span>
            </Ring>
            <div className="space-y-1 text-[12px] text-slate-600 dark:text-slate-300">
              <div className="flex justify-between gap-4"><span>Passed</span><b className="font-mono">{tests.passed}</b></div>
              <div className="flex justify-between gap-4"><span>Failed</span><b className="font-mono">{tests.total - tests.passed}</b></div>
              <div className="flex justify-between gap-4 border-t border-slate-200 pt-1 dark:border-slate-700"><span>Total</span><b className="font-mono">{tests.total}</b></div>
            </div>
          </div>
        )}
      </div>
      <div>
        <h4 className="mb-3 text-[10.5px] font-bold uppercase tracking-wider text-slate-500">Performance metrics (before → after)</h4>
        {evidence === undefined ? (
          <div className="grid grid-cols-3 gap-3">{[0, 1, 2].map((i) => <Shimmer key={i} h={58} round={10} />)}</div>
        ) : comps.length === 0 ? (
          <p className="text-xs text-slate-400">
            No measurements recorded around this deployment. Record before/after values (from CloudWatch or manually)
            via the observations API to compare them here.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            {comps.map((c) => {
              const pc = c.percent_change;
              return (
                <div key={c.metric} className="rounded-xl border border-slate-100 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
                  <div className="truncate text-[10.5px] font-semibold text-slate-500" title={c.metric}>{c.metric}</div>
                  <div className={`font-mono text-lg font-extrabold ${pc === null ? "text-slate-700 dark:text-slate-200" : pc < 0 ? "text-emerald-600" : "text-amber-600"}`}>
                    {pc === null ? "—" : `${pc > 0 ? "+" : ""}${pc.toFixed(1)}%`}
                  </div>
                  <div className="font-mono text-[10px] text-slate-400">
                    {c.before ?? "—"} → {c.after ?? "—"}{c.unit ? ` ${c.unit}` : ""} · {c.sources.join(", ")}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Step({
  n, icon: Icon, title, count, countLabel, text,
}: {
  n: number; icon: typeof Rocket; title: string; count: string; countLabel: string; text: string;
}) {
  return (
    <li className="relative rounded-xl border border-slate-100 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-900/40">
      <div className="mb-2 flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-600 text-[11px] font-bold text-white">{n}</span>
        <Icon className="h-4 w-4 text-indigo-500" />
        <span className="text-[12.5px] font-bold text-slate-800 dark:text-slate-200">{title}</span>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-xl font-extrabold text-slate-900 dark:text-white">{count}</span>
        <span className="text-[10.5px] text-slate-500">{countLabel}</span>
      </div>
      <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-600 dark:text-slate-400">{text}</p>
    </li>
  );
}

function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <span className="block text-[10px] uppercase text-slate-400">{k}</span>
      <span className={`block truncate font-semibold text-slate-700 dark:text-slate-300 ${mono ? "font-mono" : ""}`} title={v}>{v}</span>
    </div>
  );
}

function RecordForm({
  decisions, onCancel, onDone, onError,
}: {
  decisions: DecisionNode[];
  onCancel: () => void;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const preferred = decisions.find((d) => d.applied_commit_sha) ?? decisions[0];
  const [mergeId, setMergeId] = useState(preferred?.merge_id ?? "");
  const chosen = decisions.find((d) => d.merge_id === mergeId) ?? null;
  const [environment, setEnvironment] = useState<string>("staging");
  const [commit, setCommit] = useState(preferred?.applied_commit_sha ?? "");
  const [target, setTarget] = useState("");
  const [status, setStatus] = useState<string>("running");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const input = "w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (!chosen) return;
        setBusy(true);
        try {
          await createDeployment(chosen.merge_id, environment, commit.trim() || null, {
            status, target: target.trim() || null, detail: note.trim() || null,
          });
          onDone(`Recorded ${environment} deployment of decision ${chosen.merge_id.slice(0, 8)}`);
        } catch (err) {
          onError(err instanceof Error ? err.message : "Could not record the deployment");
        } finally {
          setBusy(false);
        }
      }}
      className={`${CARD} space-y-4 p-5`}
    >
      <div>
        <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Record a deployment</h2>
        <p className="text-[11.5px] text-slate-500">Log a deploy you have already made. This writes a record; it does not deploy anything.</p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className="space-y-1 md:col-span-2">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">Decision</span>
          <select
            className={input}
            value={mergeId}
            onChange={(e) => {
              setMergeId(e.target.value);
              setCommit(decisions.find((d) => d.merge_id === e.target.value)?.applied_commit_sha ?? "");
            }}
          >
            {decisions.map((d) => (
              <option key={d.merge_id} value={d.merge_id}>
                {d.merge_id.slice(0, 8)} · {d.winner_hypothesis ?? d.winner_branch_id.slice(0, 8)}
                {d.winner_score !== null ? ` · ${Math.round(d.winner_score * 100)}%` : ""}
                {d.applied_commit_sha ? ` · commit ${d.applied_commit_sha.slice(0, 8)}` : " · not applied yet"}
              </option>
            ))}
          </select>
        </label>
        {chosen && !chosen.applied_commit_sha && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11.5px] text-amber-800 md:col-span-2 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
            This decision has no validated commit yet. Normally you would run Decisions → Apply & Validate first and deploy that commit.
          </div>
        )}
        <label className="space-y-1">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">Environment</span>
          <select className={input} value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            {ENVIRONMENTS.map((env) => <option key={env} value={env}>{env}</option>)}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">Status</span>
          <select className={input} value={status} onChange={(e) => setStatus(e.target.value)}>
            {RECORD_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">Commit SHA</span>
          <input className={`${input} font-mono`} value={commit} onChange={(e) => setCommit(e.target.value)} placeholder="the commit you deployed" />
        </label>
        <label className="space-y-1">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">Target</span>
          <input className={input} value={target} onChange={(e) => setTarget(e.target.value)} placeholder="e.g. Elastic Beanstalk environment name" />
        </label>
        <label className="space-y-1 md:col-span-2">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">Note</span>
          <input className={input} value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
        </label>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          Cancel
        </button>
        <button type="submit" disabled={busy || !chosen} className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-60">
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Record deployment
        </button>
      </div>
    </form>
  );
}
