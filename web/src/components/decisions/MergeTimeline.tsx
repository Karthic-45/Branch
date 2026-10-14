import type { DecisionNode, MemoryDiff } from "../../lib/types";

/**
 * The merge, and the revert if one happened, as a single timeline.
 *
 * Everything is read from the decision and its memory diff. The revert leg
 * only appears when reverted_at is set -- an empty half-drawn timeline
 * implying an undo that never occurred would be exactly the sort of
 * suggestion this project refuses to make.
 */
export function MergeTimeline({
  node, diff,
}: { node: DecisionNode; diff: MemoryDiff | null }) {
  const reverted = Boolean(node.reverted_at);
  const losers = node.alternatives.length;
  const H = reverted ? 188 : 148;

  return (
    <figure className="rounded-[4px] border border-hairline bg-surface-2 p-3">
      <svg viewBox={`0 0 440 ${H}`} className="block h-auto w-full" role="img"
        aria-label={
          reverted
            ? `Merged, then reverted. The trunk was restored to its pre-merge state.`
            : `One winner merged into the trunk; ${losers} siblings abandoned.`
        }>
        {/* Competing branches converge */}
        {Array.from({ length: losers + 1 }, (_, i) => {
          const winner = i === losers;
          const y = 20 + i * 26;
          const color = winner ? "var(--color-success)" : "var(--color-muted)";
          return (
            <g key={i}>
              <rect x={6} y={y - 9} width={120} height={18} rx={3}
                fill="var(--color-surface)" stroke={color} strokeWidth={winner ? 1.4 : 0.8}
                strokeDasharray={winner ? undefined : "3 2"} />
              <text x={14} y={y + 4} fontSize={9} fill={winner ? "var(--color-ink)" : "var(--color-muted)"}>
                {winner ? "winner" : `abandoned ${i + 1}`}
              </text>
              <path
                d={`M 126 ${y} C 175 ${y} 175 ${20 + losers * 26} 210 ${20 + losers * 26}`}
                fill="none" stroke={color} strokeWidth={winner ? 1.6 : 0.8}
                strokeDasharray={winner ? undefined : "3 3"} opacity={winner ? 1 : 0.5} />
            </g>
          );
        })}

        {/* The transaction */}
        <rect x={210} y={20 + losers * 26 - 14} width={104} height={28} rx={4}
          fill="var(--color-accent-soft)" stroke="var(--color-accent)" strokeWidth={1} />
        <text x={262} y={20 + losers * 26 + 4} textAnchor="middle" fontSize={9.5}
          fill="var(--color-ink)">one transaction</text>

        <path d={`M 314 ${20 + losers * 26} L 356 ${20 + losers * 26}`} fill="none"
          stroke="var(--color-success)" strokeWidth={1.6} />
        <rect x={356} y={20 + losers * 26 - 14} width={78} height={28} rx={4}
          fill="var(--color-surface)" stroke="var(--color-success)" strokeWidth={1.4} />
        <text x={395} y={20 + losers * 26 + 4} textAnchor="middle" fontSize={9.5}
          fill="var(--color-ink)">trunk</text>

        {diff && (
          <text x={395} y={20 + losers * 26 + 26} textAnchor="middle" fontSize={8.5}
            fontFamily="monospace" fill="var(--color-muted)">
            {diff.parent_before_count} → {diff.parent_after_count}
          </text>
        )}

        {reverted && (
          <g>
            <path d={`M 395 ${20 + losers * 26 + 34} L 395 ${H - 34}`} fill="none"
              stroke="var(--color-warning)" strokeWidth={1.2} strokeDasharray="4 3" />
            <rect x={334} y={H - 32} width={100} height={26} rx={4}
              fill="var(--color-warning-soft)" stroke="var(--color-warning)" strokeWidth={1} />
            <text x={384} y={H - 15} textAnchor="middle" fontSize={9.5}
              fill="var(--color-ink)">reverted</text>
            <text x={12} y={H - 15} fontSize={9} fill="var(--color-ink-2)">
              trunk restored to its exact pre-merge state
            </text>
          </g>
        )}
      </svg>
      <figcaption className="mt-2 text-[12px] leading-relaxed text-ink-2">
        {reverted ? (
          <>
            Merged, then withdrawn. The <span className="font-mono">merge_log</span> row is kept and
            marked reverted — the record that a conclusion was reached and later undone survives.
          </>
        ) : (
          <>
            Promoting the winner and retiring {losers} sibling{losers === 1 ? "" : "s"} happened
            together or not at all. There is no instant in which two winners exist.
          </>
        )}
      </figcaption>
    </figure>
  );
}
