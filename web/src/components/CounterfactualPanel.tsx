import { useEffect, useState } from "react";
import { Loader2, Check, X, Scale } from "lucide-react";
import type { Counterfactual, StrategyComparison } from "../lib/types";
import { fetchCounterfactual } from "../lib/api";

/** "Why this one, and why not the others" -- every bullet traces to a stored
 * measurement. A merge from before rationale tracking says so plainly rather
 * than reconstructing an explanation after the fact. */
export function CounterfactualPanel({ mergeId }: { mergeId: string }) {
  const [cf, setCf] = useState<Counterfactual | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setCf(null);
    fetchCounterfactual(mergeId)
      .then((c) => { if (!cancelled) setCf(c); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [mergeId]);

  if (error) return <p className="p-4 font-mono text-[11.5px] text-rose">{error}</p>;
  if (!cf) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading comparison…
      </div>
    );
  }

  return (
    <div className="p-4">
      <div className="mb-3 rounded-lg border border-hairline bg-surface-2 px-3 py-2.5">
        <div className="mb-1 flex items-center gap-1.5">
          <span className="font-mono text-[10.5px] font-semibold uppercase tracking-wider text-muted">
            Selection rationale
          </span>
          {cf.tie_detected && (
            <span className="flex items-center gap-1 rounded-full border border-amber/50 bg-amber/10 px-1.5 py-0.5 font-mono text-[10px] text-amber">
              <Scale className="h-2.5 w-2.5" /> {cf.discriminator ?? "tie"}
            </span>
          )}
        </div>
        <p className="text-[12.5px] leading-relaxed text-ink-2">{cf.rationale}</p>
      </div>

      {cf.winner && <ComparisonCard c={cf.winner} won />}
      {cf.alternatives.map((a) => <ComparisonCard key={a.branch_id} c={a} />)}

      {cf.strategy_similarity.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 font-mono text-[10.5px] font-semibold uppercase tracking-wider text-muted">
            Strategy overlap
          </div>
          <div className="flex flex-wrap gap-1.5">
            {cf.strategy_similarity.map((p) => (
              <span
                key={`${p.a}-${p.b}`}
                className={`rounded-md border px-2 py-0.5 font-mono text-[10.5px] ${
                  p.cosine >= 0.9
                    ? "border-amber/50 bg-amber/10 text-amber"
                    : "border-hairline text-muted"
                }`}
              >
                {p.a} ~ {p.b} {Math.round(p.cosine * 100)}%
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ComparisonCard({ c, won = false }: { c: StrategyComparison; won?: boolean }) {
  return (
    <div className={`mb-2 rounded-lg border px-3 py-2.5 ${
      won ? "border-emerald/40 bg-emerald/[0.06]" : "border-hairline bg-surface"
    }`}>
      <div className="mb-1 flex items-start justify-between gap-2">
        <span className="flex items-start gap-1.5 text-[12.5px] font-medium text-ink">
          {won
            ? <Check className="mt-0.5 h-3.5 w-3.5 flex-none text-emerald" />
            : <X className="mt-0.5 h-3.5 w-3.5 flex-none text-muted" />}
          {c.hypothesis ?? c.strategy_name ?? c.branch_id.slice(0, 8)}
        </span>
        <span className={`flex-none font-mono text-[12px] tabular-nums ${won ? "text-emerald" : "text-muted"}`}>
          {c.score === null ? "—" : `${Math.round(c.score * 100)}%`}
        </span>
      </div>
      {c.points.length > 0 && (
        <ul className="ml-5 space-y-0.5">
          {c.points.map((p, i) => (
            <li key={i} className="text-[11.5px] text-ink-2">
              <span className="text-muted">{won ? "+" : "−"}</span> {p}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
