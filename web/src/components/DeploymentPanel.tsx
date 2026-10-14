import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, Rocket, RotateCcw, Plus, CheckCircle2, Circle } from "lucide-react";
import type { Deployment, DeploymentEnvironment, ProductionEvidence } from "../lib/types";
import {
  createDeployment, fetchDeployments, fetchDeploymentEvidence, fetchEnvironments,
  recordObservation, rollbackDeployment,
} from "../lib/api";

const STATUS_TONE: Record<string, string> = {
  pending: "border-hairline-strong text-muted",
  running: "border-amber/50 bg-amber/10 text-amber",
  healthy: "border-emerald/50 bg-emerald/10 text-emerald",
  failed: "border-rose/50 bg-rose/10 text-rose",
  rolled_back: "border-hairline-strong text-muted",
};

/** Deployment history for one decision. Recording only — this panel never
 * ships anything. Deploying stays a deliberate operator step. */
export function DeploymentPanel({ mergeId }: { mergeId: string }) {
  const [deployments, setDeployments] = useState<Deployment[] | null>(null);
  const [envs, setEnvs] = useState<DeploymentEnvironment[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [environment, setEnvironment] = useState("staging");
  const [commitSha, setCommitSha] = useState("");

  const load = useCallback(async () => {
    try {
      const [d, e] = await Promise.all([fetchDeployments(mergeId), fetchEnvironments(mergeId)]);
      setDeployments(d);
      setEnvs(e);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [mergeId]);

  useEffect(() => { void load(); }, [load]);

  async function handleRecord() {
    setBusy(true);
    setError("");
    try {
      await createDeployment(mergeId, environment, commitSha.trim() || null);
      setCommitSha("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (error && !deployments) return <p className="p-4 font-mono text-[11.5px] text-rose">{error}</p>;
  if (!deployments) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading deployments…
      </div>
    );
  }

  return (
    <div className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {envs.map((e, i) => (
          <div key={e.name} className="flex items-center gap-2">
            {i > 0 && <span className="text-muted">→</span>}
            <span className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] ${
              e.reached ? STATUS_TONE[e.latest_status ?? "pending"] : "border-hairline text-muted"
            }`}>
              {e.reached
                ? <CheckCircle2 className="h-3 w-3" />
                : <Circle className="h-3 w-3" />}
              {e.name}{e.latest_status ? ` · ${e.latest_status}` : ""}
            </span>
          </div>
        ))}
      </div>

      <div className="mb-3 flex flex-wrap items-end gap-2 rounded-lg border border-hairline bg-surface-2 p-3">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted">Environment</span>
          <select value={environment} onChange={(e) => setEnvironment(e.target.value)} className="w-36">
            <option value="development">development</option>
            <option value="staging">staging</option>
            <option value="production">production</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted">Commit (optional)</span>
          <input
            value={commitSha}
            onChange={(e) => setCommitSha(e.target.value)}
            placeholder="46d415a94ca3"
            className="w-44"
          />
        </label>
        <motion.button
          whileHover={busy ? {} : { y: -1 }}
          disabled={busy}
          onClick={handleRecord}
          className="flex items-center gap-1.5 rounded-lg border border-hairline-strong px-3 py-2 text-xs text-ink-2 transition-colors hover:border-violet hover:text-violet disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          Record deployment
        </motion.button>
        <p className="w-full text-[11px] leading-relaxed text-muted">
          Records that a deployment happened — it does not ship anything. Deploying is
          <code className="mx-1 font-mono">infra/deploy_beanstalk.sh</code>, run deliberately.
        </p>
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-rose/40 bg-rose/10 px-3 py-2 font-mono text-[11px] text-rose">
          {error}
        </div>
      )}

      {deployments.length === 0 ? (
        <p className="text-[12.5px] text-muted">No deployments recorded for this decision.</p>
      ) : (
        <ul className="space-y-2">
          {deployments.map((d) => (
            <DeploymentRow key={d.id} deployment={d} onChanged={load} />
          ))}
        </ul>
      )}
    </div>
  );
}

function DeploymentRow({ deployment, onChanged }: { deployment: Deployment; onChanged: () => void }) {
  const [evidence, setEvidence] = useState<ProductionEvidence | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [metric, setMetric] = useState("p95_latency_ms");
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");

  async function toggle() {
    setOpen((o) => !o);
    if (!evidence) setEvidence(await fetchDeploymentEvidence(deployment.id));
  }

  async function handleRollback() {
    const reason = window.prompt("Why is this being rolled back? (recorded in the audit trail)");
    if (!reason) return;
    setBusy(true);
    try {
      await rollbackDeployment(deployment.id, reason);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function handleObservations() {
    setBusy(true);
    try {
      for (const [phase, raw] of [["before", before], ["after", after]] as const) {
        if (!raw.trim()) continue;
        await recordObservation(deployment.id, metric, phase, Number(raw), "manual");
      }
      setBefore("");
      setAfter("");
      setEvidence(await fetchDeploymentEvidence(deployment.id));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-lg border border-hairline bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button onClick={toggle} className="flex items-center gap-2 text-left">
          <Rocket className="h-3.5 w-3.5 flex-none text-violet" />
          <span className="font-mono text-[11.5px] text-ink-2">{deployment.environment}</span>
          <span className={`rounded-full border px-2 py-0.5 font-mono text-[10px] ${STATUS_TONE[deployment.status]}`}>
            {deployment.status}
          </span>
          {deployment.commit_sha && (
            <span className="font-mono text-[11px] text-muted">{deployment.commit_sha.slice(0, 12)}</span>
          )}
        </button>
        {deployment.status !== "rolled_back" && (
          <button
            onClick={handleRollback}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-lg border border-hairline-strong px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-rose hover:text-rose disabled:opacity-50"
          >
            <RotateCcw className="h-3 w-3" /> Roll back
          </button>
        )}
      </div>

      {deployment.detail && (
        <p className="mt-1 text-[11.5px] text-muted">{deployment.detail}</p>
      )}

      {open && (
        <div className="mt-2.5 border-t border-hairline pt-2.5">
          {evidence?.comparisons.length ? (
            <table className="mb-2 w-full border-collapse text-[11.5px]">
              <tbody>
                {evidence.comparisons.map((c) => (
                  <tr key={c.metric} className="border-b border-hairline last:border-none">
                    <td className="py-1 pr-3 font-mono text-muted">{c.metric}</td>
                    <td className="py-1 font-mono text-ink-2">
                      {c.before ?? "—"} → {c.after ?? "—"} {c.unit}
                    </td>
                    <td className={`py-1 text-right font-mono ${
                      c.delta === null ? "text-muted" : c.delta < 0 ? "text-emerald" : "text-amber"
                    }`}>
                      {c.percent_change === null ? "—" : `${c.percent_change.toFixed(1)}%`}
                    </td>
                    <td className="py-1 pl-3 text-right font-mono text-[10px] text-muted">
                      {c.sources.join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="mb-2 text-[11.5px] text-muted">No measurements recorded.</p>
          )}

          <div className="flex flex-wrap items-end gap-2">
            <input value={metric} onChange={(e) => setMetric(e.target.value)} className="w-40" placeholder="metric" />
            <input value={before} onChange={(e) => setBefore(e.target.value)} className="w-24" placeholder="before" />
            <input value={after} onChange={(e) => setAfter(e.target.value)} className="w-24" placeholder="after" />
            <button
              onClick={handleObservations}
              disabled={busy}
              className="rounded-lg border border-hairline-strong px-2.5 py-1.5 text-[11px] text-ink-2 hover:border-violet hover:text-violet disabled:opacity-50"
            >
              Record measurement
            </button>
          </div>
          {evidence && <p className="mt-2 text-[11px] leading-relaxed text-muted">{evidence.note}</p>}
        </div>
      )}
    </li>
  );
}
