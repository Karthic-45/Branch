import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Play, Loader2, ShieldCheck, ShieldAlert,
  Lock, Copy, Clock, Zap, ArrowRight, CircleDashed,
} from "lucide-react";
import {
  fetchClusterStats, runCowProof, runIsolationProof, runResilienceProof, runTemporalProof,
} from "../../lib/api";
import type {
  ClusterStats, CopyOnWriteProof, IsolationProof, ProofStep, ResilienceProof, TemporalProof,
} from "../../lib/types";

/* Every number on this page comes from a proof the backend just ran against
   the live cluster. Before a proof has run, its card says so -- it does not
   show the result it is expected to produce. */

interface Props {
  onToast?: (text: string, tone?: "success" | "error" | "neutral") => void;
}

// Parameters sent to the backend -- shown on the cards so the reader can see
// what was asked for, separately from what was measured.
const COW_SEED_ROWS = 200;
const COW_FORKS = 50;
const RES_BRANCHES = 5;
const RES_FAILURES = 1;

type Verdict = "verified" | "failed";

function verdictToast(
  onToast: Props["onToast"], name: string, verdict: Verdict, headline: string,
) {
  onToast?.(`${name}: ${headline}`, verdict === "verified" ? "success" : "error");
}

export function ProofsLab({ onToast }: Props) {
  const [iso, setIso] = useState<IsolationProof | null>(null);
  const [cow, setCow] = useState<CopyOnWriteProof | null>(null);
  const [temp, setTemp] = useState<TemporalProof | null>(null);
  const [res, setRes] = useState<ResilienceProof | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [cluster, setCluster] = useState<ClusterStats | null>(null);

  useEffect(() => {
    fetchClusterStats().then(setCluster).catch(() => setCluster(null));
  }, []);

  async function run<T extends { verdict: Verdict; headline: string }>(
    key: string, name: string, fn: () => Promise<T>, set: (v: T) => void,
  ) {
    setRunning(key);
    setErrors((e) => ({ ...e, [key]: "" }));
    try {
      const result = await fn();
      set(result);
      verdictToast(onToast, name, result.verdict, result.headline);
    } catch (err) {
      const msg = err instanceof Error ? err.message : `${name} failed to run`;
      setErrors((e) => ({ ...e, [key]: msg }));
      onToast?.(`${name}: ${msg}`, "error");
    } finally {
      setRunning(null);
    }
  }

  const latencies = cow?.fork_latency_ms ?? [];
  const avg = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null;
  const sorted = [...latencies].sort((a, b) => a - b);
  const p95 = sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] : null;
  const max = sorted.length ? sorted[sorted.length - 1] : null;
  const gcHours = cluster?.gc_ttl_seconds != null ? cluster.gc_ttl_seconds / 3600 : null;

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      <div>
        <h1 className="text-xl sm:text-2xl font-extrabold text-slate-900 dark:text-white">
          Interactive Verification Lab
        </h1>
        <p className="text-xs text-slate-500 mt-1">
          Each proof runs live against the CockroachDB cluster when you press Run. Results below are what that run measured.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* ── 1. Isolation ── */}
        <ProofCard
          n={1}
          title="Isolation Proof"
          blurb="Writes a secret belief on Branch A, then searches for it from sibling Branch B."
          verdict={iso?.verdict}
          running={running === "iso"}
          disabled={running !== null}
          error={errors.iso}
          onRun={() => run("iso", "Isolation proof", runIsolationProof, setIso)}
          footerLeft={<><Lock className="h-3.5 w-3.5" /> Lineage-scoped vector search</>}
          footerRight={iso ? `${iso.hits_from_b} leaked` : "not run"}
        >
          {iso ? (
            <div className="space-y-3">
              <Row label="Secret written on Branch A" value={<code className="text-indigo-600 dark:text-indigo-400">{iso.secret}</code>} />
              <div className="grid grid-cols-2 gap-2">
                <Stat label={`Branch A ${iso.branch_a.slice(0, 8)} hits`} value={iso.hits_from_a} tone={iso.hits_from_a > 0 ? "ok" : "bad"} />
                <Stat label={`Branch B ${iso.branch_b.slice(0, 8)} hits`} value={iso.hits_from_b} tone={iso.hits_from_b === 0 ? "ok" : "bad"} />
              </div>
              <Headline verdict={iso.verdict} text={iso.headline} />
              <Steps steps={iso.steps} />
            </div>
          ) : <NotRun />}
        </ProofCard>

        {/* ── 2. Copy-on-write ── */}
        <ProofCard
          n={2}
          title="Copy-on-Write Proof"
          blurb={`Seeds a parent with ${COW_SEED_ROWS} memories, forks it ${COW_FORKS} times, and counts memory rows added by forking.`}
          verdict={cow?.verdict}
          running={running === "cow"}
          disabled={running !== null}
          error={errors.cow}
          onRun={() => run("cow", "Copy-on-write proof", () => runCowProof(COW_SEED_ROWS, COW_FORKS), setCow)}
          footerLeft={<><Copy className="h-3.5 w-3.5" /> A fork writes one branch row</>}
          footerRight={cow ? `${cow.memory_rows_added_by_forking} rows copied` : "not run"}
        >
          {cow ? (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <Stat label="Parent rows" value={cow.parent_rows} />
                <Stat label="Forks created" value={cow.forks_created} />
                <Stat label="Rows duplicated" value={cow.memory_rows_added_by_forking} tone={cow.memory_rows_added_by_forking === 0 ? "ok" : "bad"} />
              </div>
              {avg !== null && (
                <div className="rounded-lg bg-white dark:bg-slate-900 p-3 border border-slate-100 dark:border-slate-800 space-y-2">
                  <div className="flex items-center justify-between text-xs font-semibold">
                    <span className="text-slate-600 dark:text-slate-300">Fork latency ({latencies.length} forks)</span>
                    <span className="font-mono text-slate-700 dark:text-slate-200">
                      avg {avg.toFixed(1)} ms · p95 {p95?.toFixed(1)} ms · max {max?.toFixed(1)} ms
                    </span>
                  </div>
                  <LatencyStrip values={latencies} max={max ?? 1} />
                </div>
              )}
              <Headline verdict={cow.verdict} text={cow.headline} />
              <Steps steps={cow.steps} />
            </div>
          ) : <NotRun />}
        </ProofCard>

        {/* ── 3. Temporal ── */}
        <ProofCard
          n={3}
          title="Temporal Proof"
          blurb="Performs a real merge, then reads the parent's belief set as it was before the merge."
          verdict={temp?.verdict}
          running={running === "temp"}
          disabled={running !== null}
          error={errors.temp}
          onRun={() => run("temp", "Temporal proof", runTemporalProof, setTemp)}
          footerLeft={<><Clock className="h-3.5 w-3.5" /> AS OF SYSTEM TIME</>}
          footerRight={gcHours !== null ? `GC window ${gcHours % 1 === 0 ? gcHours : gcHours.toFixed(1)} h (live)` : "GC window unknown"}
        >
          {temp ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <Stat label={`Before merge · ${temp.before_merge.source}`} value={`${temp.before_merge.count} keys`} />
                <Stat label={`After merge · ${temp.after_merge.source}`} value={`${temp.after_merge.count} keys`} />
              </div>
              <KeyDiff before={temp.before_merge.keys} after={temp.after_merge.keys} />
              <Row label="Merge" value={<code>{temp.merge_id.slice(0, 8)}</code>} />
              <Headline verdict={temp.verdict} text={temp.headline} />
              <Steps steps={temp.steps} />
            </div>
          ) : <NotRun />}
        </ProofCard>

        {/* ── 4. Resilience ── */}
        <ProofCard
          n={4}
          title="Resilience Proof"
          blurb={`Starts ${RES_BRANCHES} branch workers, injects ${RES_FAILURES} crash, and checks the tree and audit trail stay consistent.`}
          verdict={res?.verdict}
          running={running === "res"}
          disabled={running !== null}
          error={errors.res}
          onRun={() => run("res", "Resilience proof", () => runResilienceProof(RES_BRANCHES, RES_FAILURES), setRes)}
          footerLeft={<><Zap className="h-3.5 w-3.5" /> No partial state</>}
          footerRight={res ? `${res.branches_completed}/${res.branches_started} completed` : "not run"}
        >
          {res ? (
            <div className="space-y-3">
              <div className="space-y-1.5">
                {res.outcomes.map((o) => (
                  <div
                    key={o.branch_id}
                    className={`flex items-center justify-between gap-3 rounded-lg p-2 text-xs border ${
                      o.survived
                        ? "border-emerald-200 dark:border-emerald-900 bg-emerald-50/40 dark:bg-emerald-950/20 text-emerald-700 dark:text-emerald-300"
                        : "border-rose-200 dark:border-rose-900 bg-rose-50/40 dark:bg-rose-950/20 text-rose-700 dark:text-rose-300"
                    }`}
                  >
                    <span className="font-mono">{o.strategy} · {o.branch_id.slice(0, 8)}</span>
                    <span className="min-w-0 truncate text-right text-[11px] font-semibold" title={o.detail}>
                      {o.injected_failure ? `injected: ${o.injected_failure}` : o.detail}
                    </span>
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Tree consistent" value={res.tree_consistent ? "yes" : "no"} tone={res.tree_consistent ? "ok" : "bad"} />
                <Stat label="Audit consistent" value={res.audit_consistent ? "yes" : "no"} tone={res.audit_consistent ? "ok" : "bad"} />
              </div>
              <Headline verdict={res.verdict} text={res.headline} />
              <Steps steps={res.steps} />
            </div>
          ) : <NotRun />}
        </ProofCard>
      </div>
    </div>
  );
}

function ProofCard({
  n, title, blurb, verdict, running, disabled, error, onRun, footerLeft, footerRight, children,
}: {
  n: number;
  title: string;
  blurb: string;
  verdict?: Verdict;
  running: boolean;
  disabled: boolean;
  error?: string;
  onRun: () => void;
  footerLeft: React.ReactNode;
  footerRight: string;
  children: React.ReactNode;
}) {
  const badge = running
    ? { text: "Running", cls: "bg-indigo-100 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300" }
    : verdict === "verified"
      ? { text: "Verified", cls: "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400" }
      : verdict === "failed"
        ? { text: "Failed", cls: "bg-rose-100 dark:bg-rose-950/60 text-rose-700 dark:text-rose-400" }
        : { text: "Not run", cls: "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400" };
  return (
    <motion.div
      whileHover={{ y: -2 }}
      className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs flex flex-col justify-between space-y-5"
    >
      <div>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-slate-900 dark:text-white">{n}. {title}</h2>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${badge.cls}`}>{badge.text}</span>
            </div>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{blurb}</p>
          </div>
          <button
            onClick={onRun}
            disabled={disabled}
            className="flex flex-none items-center gap-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-4 py-2 text-xs shadow-xs transition-transform active:scale-95 disabled:opacity-50"
          >
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5 fill-current" />}
            <span>{running ? "Running…" : verdict ? "Run again" : "Run proof"}</span>
          </button>
        </div>
        <div className="mt-5 rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-4">
          {error && (
            <div className="mb-3 rounded-lg border border-rose-200 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/30 p-2.5 text-xs text-rose-700 dark:text-rose-300 break-words">
              {error}
            </div>
          )}
          {children}
        </div>
      </div>
      <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-800 text-[11px] text-slate-400">
        <span className="flex items-center gap-1 font-semibold text-slate-500 dark:text-slate-400">{footerLeft}</span>
        <span className="font-mono">{footerRight}</span>
      </div>
    </motion.div>
  );
}

function NotRun() {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 py-6 text-center">
      <CircleDashed className="h-5 w-5 text-slate-300 dark:text-slate-600" />
      <p className="text-xs font-medium text-slate-500">Not run yet</p>
      <p className="text-[11px] text-slate-400">Press Run proof to execute it against the live cluster.</p>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "ok" | "bad" }) {
  const color = tone === "ok" ? "text-emerald-600" : tone === "bad" ? "text-rose-600" : "text-slate-900 dark:text-white";
  return (
    <div className="rounded-lg bg-white dark:bg-slate-900 p-2.5 border border-slate-100 dark:border-slate-800 text-center">
      <div className="truncate text-[10px] text-slate-400 uppercase font-medium" title={label}>{label}</div>
      <div className={`text-lg font-extrabold font-mono ${color}`}>{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 dark:border-slate-800 bg-white dark:bg-slate-900 p-2.5 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="min-w-0 truncate font-mono font-bold">{value}</span>
    </div>
  );
}

function Headline({ verdict, text }: { verdict: Verdict; text: string }) {
  const ok = verdict === "verified";
  return (
    <div className={`flex items-start gap-2 rounded-lg border p-2.5 text-xs font-semibold ${
      ok
        ? "border-emerald-200 dark:border-emerald-900 bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-300"
        : "border-rose-200 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300"
    }`}>
      {ok ? <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-none" /> : <ShieldAlert className="mt-0.5 h-3.5 w-3.5 flex-none" />}
      <span>{text}</span>
    </div>
  );
}

function Steps({ steps }: { steps: ProofStep[] }) {
  if (!steps.length) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer select-none text-[11px] font-semibold text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
        {steps.length} recorded steps
      </summary>
      <ol className="mt-2 space-y-1">
        {steps.map((s, i) => (
          <li key={i} className="flex items-start gap-2">
            <ArrowRight className="mt-0.5 h-3 w-3 flex-none text-slate-400" />
            <span className="min-w-0 flex-1">
              <span className="font-semibold text-slate-700 dark:text-slate-200">{s.step}</span>
              <span className="text-slate-500"> — {s.detail}</span>
            </span>
            <span className="flex-none font-mono text-[10.5px] text-slate-400">{s.elapsed_ms.toFixed(0)} ms</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function LatencyStrip({ values, max }: { values: number[]; max: number }) {
  return (
    <div className="flex h-8 items-end gap-px" aria-label="Per-fork latency">
      {values.map((v, i) => (
        <div
          key={i}
          className="flex-1 rounded-t-sm bg-emerald-500/80"
          style={{ height: `${Math.max(6, (v / max) * 100)}%` }}
          title={`fork ${i + 1}: ${v.toFixed(1)} ms`}
        />
      ))}
    </div>
  );
}

function KeyDiff({ before, after }: { before: string[]; after: string[] }) {
  const prior = new Set(before);
  const added = after.filter((k) => !prior.has(k));
  if (!added.length) return null;
  return (
    <div className="rounded-lg border border-slate-100 dark:border-slate-800 bg-white dark:bg-slate-900 p-2.5 text-xs">
      <div className="mb-1 text-[10px] font-medium uppercase text-slate-400">Keys the merge added</div>
      <div className="flex flex-wrap gap-1">
        {added.map((k) => (
          <code key={k} className="rounded bg-indigo-50 dark:bg-indigo-950/40 px-1.5 py-0.5 text-[10.5px] text-indigo-700 dark:text-indigo-300">{k}</code>
        ))}
      </div>
    </div>
  );
}
