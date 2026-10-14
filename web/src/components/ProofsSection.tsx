import { useState } from "react";
import type { Tone } from "./ui";
import { CopyOnWriteDiagram, IsolationDiagram } from "./proofs/ProofDiagrams";

type Toast = (text: string, tone?: Tone) => void;
import { motion, AnimatePresence } from "framer-motion";
import {
  Loader2, ShieldCheck, XCircle, Play, Layers, Clock, Copy, Zap,
} from "lucide-react";
import type {
  CopyOnWriteProof, IsolationProof, ProofStep, ResilienceProof, TemporalProof,
} from "../lib/types";
import {
  runCowProof, runIsolationProof, runResilienceProof, runTemporalProof,
} from "../lib/api";

type AnyProof = IsolationProof | CopyOnWriteProof | TemporalProof | ResilienceProof;

/** The three claims this project rests on, each executed live rather than
 * asserted. Every run creates real branches on the real cluster, measures
 * what happened, and cleans up after itself. */
export function ProofsSection({ onToast }: { onToast?: Toast } = {}) {
  return (
    <div className="space-y-4">
      <p className="text-[13px] leading-relaxed text-ink-2">
        Each of these runs against the live CockroachDB cluster right now — creating real
        branches, running real queries, and reporting what actually happened, step by step.
        Scratch branches are removed when the proof finishes.
      </p>

      <ProofCard
          onToast={onToast}
        title="Branch isolation"
        icon={Layers}
        claim="Branch B cannot retrieve Branch A's private belief. Same database, same vector index, same query."
        run={runIsolationProof}
        render={(p) => {
          const proof = p as IsolationProof;
          return (
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Hits from branch A" value={proof.hits_from_a} tone="emerald" />
              <Stat
                label="Hits from branch B"
                value={proof.hits_from_b}
                tone={proof.hits_from_b === 0 ? "emerald" : "rose"}
              />
              <div className="col-span-2"><IsolationDiagram proof={proof} /></div>
            </div>
          );
        }}
      />

      <ProofCard
          onToast={onToast}
        title="Copy-on-write forking"
        icon={Copy}
        claim="Forking inherits a parent's entire memory without copying any of it. A fork is one row."
        run={() => runCowProof(200, 5)}
        render={(p) => {
          const proof = p as CopyOnWriteProof;
          return (
            <>
              <div className="grid grid-cols-3 gap-2">
              <Stat label="Parent memories" value={proof.parent_rows} />
              <Stat label="Forks created" value={proof.forks_created} />
              <Stat
                label="Rows copied"
                value={proof.memory_rows_added_by_forking}
                tone={proof.memory_rows_added_by_forking === 0 ? "emerald" : "rose"}
              />
              <div className="col-span-3 rounded-lg border border-hairline bg-surface-2 px-3 py-2">
                <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-muted">
                  Fork latency — flat regardless of how much memory was inherited
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {proof.fork_latency_ms.map((ms, i) => (
                    <span key={i} className="rounded border border-hairline px-1.5 py-0.5 font-mono text-[11px] text-ink-2">
                      {ms}ms
                    </span>
                  ))}
                </div>
              </div>
              </div>
              <CopyOnWriteDiagram proof={proof} />
            </>
          );
        }}
      />

      <ProofCard
          onToast={onToast}
        title="Temporal memory"
        icon={Clock}
        claim="After a merge, the parent's earlier belief state is still readable. Nothing was overwritten."
        run={runTemporalProof}
        render={(p) => {
          const proof = p as TemporalProof;
          return (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {[proof.before_merge, proof.after_merge].map((snap) => (
                <div key={snap.label} className="rounded-lg border border-hairline bg-surface-2 px-3 py-2">
                  <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-muted">
                    {snap.label} · {snap.count} beliefs
                  </div>
                  <ul className="space-y-0.5">
                    {snap.keys.map((k) => (
                      <li key={k} className="font-mono text-[11.5px] text-ink-2">{k}</li>
                    ))}
                  </ul>
                  <div className="mt-1 text-[10.5px] italic text-muted">{snap.source}</div>
                </div>
              ))}
            </div>
          );
        }}
      />
      <ProofCard
          onToast={onToast}
        title="Resilience under failure"
        icon={Zap}
        claim="Workers can die mid-run without corrupting the decision graph or stranding partial state."
        run={() => runResilienceProof(5, 2)}
        render={(p) => {
          const proof = p as ResilienceProof;
          return (
            <>
              <div className="mb-2 grid grid-cols-3 gap-2">
                <Stat label="Workers started" value={proof.branches_started} />
                <Stat label="Failures injected" value={proof.failures_injected} tone="rose" />
                <Stat label="Completed" value={proof.branches_completed} tone="emerald" />
              </div>
              <ul className="space-y-1">
                {proof.outcomes.map((o) => (
                  <li key={o.branch_id} className="flex items-start gap-2 text-[11.5px]">
                    {o.survived
                      ? <ShieldCheck className="mt-0.5 h-3 w-3 flex-none text-emerald" />
                      : <XCircle className="mt-0.5 h-3 w-3 flex-none text-rose" />}
                    <span className="font-mono text-muted">{o.strategy}</span>
                    <span className="min-w-0 text-ink-2">{o.detail}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] leading-relaxed text-muted">
                Failures are injected in application code only — never against the cluster or a
                real build.
              </p>
            </>
          );
        }}
      />
    </div>
  );
}

function ProofCard({
  title, icon: Icon, claim, run, render, onToast,
}: {
  title: string;
  icon: typeof Layers;
  claim: string;
  run: () => Promise<AnyProof>;
  render: (proof: AnyProof) => React.ReactNode;
  onToast?: Toast;
}) {
  const [busy, setBusy] = useState(false);
  const [proof, setProof] = useState<AnyProof | null>(null);
  const [error, setError] = useState("");

  async function go() {
    setBusy(true);
    setError("");
    setProof(null);
    onToast?.(`${title}: running against the live cluster`, "running");
    try {
      const result = await run();
      setProof(result);
      // The toast says it finished; the verdict and every timing stay on the
      // page. A result that vanishes in four seconds is not evidence.
      onToast?.(
        `${title}: ${result.verdict === "verified" ? "verified" : "did not verify"}`,
        result.verdict === "verified" ? "success" : "error",
      );
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(message);
      onToast?.(`${title} failed to run`, "error");
    } finally {
      setBusy(false);
    }
  }

  const verified = proof?.verdict === "verified";

  return (
    <div className="rounded-[6px] border border-hairline bg-surface p-4">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2">
            <Icon className="h-4 w-4 flex-none text-violet" />
            <h3 className="text-[14px] font-semibold text-ink">{title}</h3>
            {proof && (
              <span className={`flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${
                verified
                  ? "border-emerald/50 bg-emerald/10 text-emerald"
                  : "border-rose/50 bg-rose/10 text-rose"
              }`}>
                {verified ? <ShieldCheck className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                {proof.verdict}
              </span>
            )}
          </div>
          <p className="text-[12.5px] leading-relaxed text-ink-2">{claim}</p>
        </div>
        <motion.button
          whileHover={busy ? {} : { y: -1 }}
          whileTap={busy ? {} : { scale: 0.97 }}
          disabled={busy}
          onClick={go}
          className="flex flex-none items-center gap-1.5 rounded-lg bg-violet px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-violet/90 disabled:opacity-60"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          {busy ? "Running…" : "Run proof"}
        </motion.button>
      </div>

      {error && (
        <div className="mt-2 rounded-lg border border-rose/40 bg-rose/10 px-3 py-2 font-mono text-[11px] text-rose">
          {error}
        </div>
      )}

      <AnimatePresence>
        {proof && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className={`mt-3 rounded-lg border px-3 py-2.5 text-[12.5px] leading-relaxed ${
              verified ? "border-emerald/40 bg-emerald/[0.06] text-ink-2" : "border-rose/40 bg-rose/10 text-rose"
            }`}>
              {proof.headline}
            </div>
            <div className="mt-3">{render(proof)}</div>
            <StepLog steps={proof.steps} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function StepLog({ steps }: { steps: ProofStep[] }) {
  return (
    <details className="mt-3">
      <summary className="cursor-pointer font-mono text-[10.5px] uppercase tracking-wider text-muted hover:text-ink-2">
        What it did ({steps.length} steps)
      </summary>
      <ol className="mt-1.5 space-y-1">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2 font-mono text-[11px]">
            <span className="w-16 flex-none text-right text-muted tabular-nums">
              {s.elapsed_ms.toFixed(0)}ms
            </span>
            <span className="w-20 flex-none text-violet">{s.step}</span>
            <span className="min-w-0 text-ink-2">{s.detail}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "emerald" | "rose" }) {
  const color = tone === "emerald" ? "text-emerald" : tone === "rose" ? "text-rose" : "text-ink";
  return (
    <div className="rounded-lg border border-hairline bg-surface-2 px-3 py-2">
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`font-mono text-[20px] font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
