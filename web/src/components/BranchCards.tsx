import { motion } from "framer-motion";
import type { BranchNode, Stage } from "../lib/types";
import { pct, STEP_LABEL, STEPS, stepFor } from "../lib/stages";

interface Props {
  nodes: BranchNode[];
  branchStage: Map<string, Stage>;
}

export function BranchCards({ nodes, branchStage }: Props) {
  const children = nodes
    .filter((n) => n.parent_id !== null)
    .sort((a, b) => (b.score ?? -1) - (a.score ?? -1));

  if (!children.length) return null;

  return (
    <section className="mt-5">
      <div className="mb-2.5 flex flex-wrap items-baseline gap-2.5">
        <h2 className="font-semibold text-ink">Branches</h2>
        <span className="text-[12.5px] text-muted">
          Each one is an isolated belief state — it cannot read its siblings' conclusions.
        </span>
      </div>
      <div className="card flex flex-col divide-y divide-hairline overflow-hidden rounded-2xl">
        {children.map((n) => (
          <BranchCard key={n.id} node={n} stage={branchStage.get(n.id)} />
        ))}
      </div>
    </section>
  );
}

function BranchCard({ node, stage }: { node: BranchNode; stage: Stage | undefined }) {
  const p = pct(node.score);
  const isWinner = node.status === "merged";
  const abandoned = node.status === "abandoned";
  const currentStep = stepFor(stage ?? (node.status !== "exploring" ? "merged" : undefined));
  const idx = STEPS.indexOf(currentStep);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className={`relative p-4 transition-colors ${isWinner ? "bg-success-soft" : ""}`}
    >
      {isWinner && <span className="absolute inset-y-0 left-0 w-[3px] bg-success" />}

      <div className="mb-3 flex items-baseline gap-2.5">
        <span className={`flex-1 text-sm font-semibold ${abandoned ? "text-ink-2" : "text-ink"}`}>
          {node.hypothesis ?? "—"}
        </span>
        <span className="flex-none font-mono text-[10.5px] text-muted">{node.id.slice(0, 8)}</span>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-y-2">
        {STEPS.map((step, i) => {
          const done = i < idx;
          const active = i === idx && node.status === "exploring";
          const good = i === idx && step === "resolved" && isWinner;
          const bad = i === idx && step === "resolved" && abandoned;
          const label = step === "resolved" && i === idx
            ? (good ? "Merged" : bad ? "Abandoned" : "Merge")
            : STEP_LABEL[step];
          return (
            <div key={step} className="flex items-center">
              <div className="flex items-center gap-1.5">
                <span className="relative flex h-2.5 w-2.5 flex-none items-center justify-center">
                  {active && (
                    <motion.span
                      className="absolute h-full w-full rounded-full bg-accent"
                      animate={{ scale: [1, 2.3], opacity: [0.55, 0] }}
                      transition={{ repeat: Infinity, duration: 1.4 }}
                    />
                  )}
                  <span
                    className={`relative box-border h-2.5 w-2.5 flex-none rounded-full border-[1.5px] ${
                      good ? "border-success bg-success"
                        : bad ? "border-dashed border-muted bg-transparent"
                        : done ? "border-accent bg-accent"
                        : active ? "border-accent bg-plane"
                        : "border-hairline-strong bg-plane"
                    }`}
                  />
                </span>
                <span className={`whitespace-nowrap font-mono text-[9px] ${
                  good ? "text-success" : active ? "font-semibold text-accent" : done ? "text-ink-2" : "text-muted"
                }`}>
                  {label}
                </span>
              </div>
              {i < STEPS.length - 1 && (
                <span className={`mx-1.5 h-px w-5 flex-none ${done ? "bg-accent" : "bg-hairline-strong"}`} />
              )}
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2.5">
        <span className="w-20 flex-none font-mono text-[11px] text-muted">{node.status}</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-hairline">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: p === null ? "0%" : `${p}%` }}
            transition={{ duration: 0.7, ease: [0.4, 0, 0.2, 1] }}
            className={`h-full rounded-full ${
              isWinner ? "bg-success"
                : abandoned ? "bg-muted"
                : "bg-accent"
            }`}
          />
        </div>
        <span className={`w-10 flex-none text-right font-mono text-xs tabular-nums ${isWinner ? "text-success" : "text-ink-2"}`}>
          {p === null ? "—" : `${p}%`}
        </span>
      </div>

      {node.reason && (
        <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-hairline/60 pt-2 text-[11px]">
          <span className="font-sans text-[10px] uppercase tracking-wider text-muted font-semibold">Reason:</span>
          <span
            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono text-[10.5px] ${
              isWinner
                ? "bg-success-soft text-success border border-success/30"
                : node.reason === "patch_did_not_apply"
                ? "bg-error-soft text-error border border-error/30"
                : "bg-warning-soft text-warning border border-warning/30"
            }`}
          >
            {node.passed != null && node.total != null && node.total > 0
              ? `${node.passed}/${node.total} tests (${node.reason})`
              : node.reason}
          </span>
        </div>
      )}
    </motion.div>
  );
}
