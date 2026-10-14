import { CheckCircle2, XCircle, Clock } from "lucide-react";
import type { BranchVerdict } from "../../lib/types";

/**
 * "Why merged" / "Why abandoned" for one branch, exactly as the backend
 * worded it from the recorded outcome and decision rationale. This component
 * adds no wording of its own beyond the label, so what it says is always
 * traceable to a stored measurement.
 */
const TONE = {
  merged: {
    label: "Why merged",
    Icon: CheckCircle2,
    box: "border-emerald-200 dark:border-emerald-900/60 bg-emerald-50/70 dark:bg-emerald-950/30",
    head: "text-emerald-700 dark:text-emerald-400",
  },
  abandoned: {
    label: "Why abandoned",
    Icon: XCircle,
    box: "border-rose-200 dark:border-rose-900/60 bg-rose-50/70 dark:bg-rose-950/30",
    head: "text-rose-700 dark:text-rose-400",
  },
  pending: {
    label: "No decision yet",
    Icon: Clock,
    box: "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50",
    head: "text-slate-500",
  },
} as const;

export function VerdictBox({
  verdict, compact = false, title,
}: { verdict: BranchVerdict; compact?: boolean; title?: string }) {
  const t = TONE[verdict.decision];
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${t.box}`} onClick={(e) => compact && e.stopPropagation()}>
      <div className={`mb-1 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider ${t.head}`}>
        <t.Icon className="h-3.5 w-3.5 flex-none" />
        <span>{title ?? t.label}</span>
      </div>
      <p
        className={`text-[12px] leading-relaxed text-slate-700 dark:text-slate-200 ${compact ? "line-clamp-5" : ""}`}
        title={compact ? verdict.headline : undefined}
      >
        {verdict.headline}
      </p>
      {!compact && verdict.reasons.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-black/5 dark:border-white/5 pt-2">
          {verdict.reasons.map((r) => (
            <li key={r} className="flex gap-1.5 text-[11.5px] text-slate-600 dark:text-slate-300">
              <span className="text-slate-400">•</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
