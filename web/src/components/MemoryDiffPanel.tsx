import { useEffect, useState } from "react";
import { Loader2, Plus, Pencil, Minus } from "lucide-react";
import type { MemoryDelta, MemoryDiff } from "../lib/types";
import { fetchMemoryDiff } from "../lib/api";

/** Version control for beliefs, shown as a diff: what the parent held before
 * this merge, what the winning branch contributed, and what actually changed.
 * Scoped to this merge alone, so it stays readable even after a revert. */
export function MemoryDiffPanel({ mergeId }: { mergeId: string }) {
  const [diff, setDiff] = useState<MemoryDiff | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setDiff(null);
    fetchMemoryDiff(mergeId)
      .then((d) => { if (!cancelled) setDiff(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [mergeId]);

  if (error) return <p className="p-4 font-mono text-[11.5px] text-rose">{error}</p>;
  if (!diff) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading memory diff…
      </div>
    );
  }

  const nothingMoved = !diff.added.length && !diff.changed.length && !diff.tombstoned.length;

  return (
    <div className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 font-mono text-[11.5px]">
        <Count label="parent before" value={diff.parent_before_count} />
        <span className="text-muted">+</span>
        <Count label="branch" value={diff.branch_count} accent />
        <span className="text-muted">→</span>
        <Count label="parent after" value={diff.parent_after_count} />
        <span className="ml-1 text-muted">{diff.unchanged_count} untouched</span>
      </div>

      {nothingMoved ? (
        <p className="text-[12.5px] text-muted">
          This merge changed no beliefs — the winner's rows were already present and identical.
        </p>
      ) : (
        <div className="space-y-3">
          <Group title="Added" deltas={diff.added} icon={Plus} tone="emerald" />
          <Group title="Changed" deltas={diff.changed} icon={Pencil} tone="amber" />
          <Group title="Tombstoned" deltas={diff.tombstoned} icon={Minus} tone="rose" />
        </div>
      )}
    </div>
  );
}

function Count({ label, value, accent = false }: { label: string; value: number; accent?: boolean }) {
  return (
    <span className={`rounded-md border px-2 py-0.5 ${
      accent ? "border-violet/50 bg-violet/10 text-violet" : "border-hairline text-ink-2"
    }`}>
      {value} <span className="text-muted">{label}</span>
    </span>
  );
}

const TONE: Record<string, string> = {
  emerald: "border-emerald/40 bg-emerald/[0.06] text-emerald",
  amber: "border-amber/40 bg-amber/[0.06] text-amber",
  rose: "border-rose/40 bg-rose/[0.06] text-rose",
};

function Group({
  title, deltas, icon: Icon, tone,
}: {
  title: string;
  deltas: MemoryDelta[];
  icon: typeof Plus;
  tone: keyof typeof TONE;
}) {
  if (!deltas.length) return null;
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        <Icon className={`h-3.5 w-3.5 ${TONE[tone].split(" ").pop()}`} strokeWidth={2.5} />
        <span className="font-mono text-[10.5px] font-semibold uppercase tracking-wider text-muted">
          {title} ({deltas.length})
        </span>
      </div>
      <ul className="space-y-1.5">
        {deltas.map((d) => (
          <li key={d.key} className={`rounded-lg border px-3 py-2 ${TONE[tone]}`}>
            <div className="font-mono text-[11px] font-medium">{d.key}</div>
            {d.before && (
              <div className="mt-1 text-[11.5px] text-ink-2">
                <span className="font-mono text-muted">− </span>
                <span className="line-through opacity-70">{d.before}</span>
              </div>
            )}
            {d.after && (
              <div className="mt-0.5 text-[11.5px] text-ink-2">
                <span className="font-mono text-muted">+ </span>
                {d.after}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
