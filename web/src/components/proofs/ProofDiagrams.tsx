import type { CopyOnWriteProof, IsolationProof } from "../../lib/types";

/**
 * The two proofs whose result is a shape rather than a number.
 *
 * Both read exclusively from the proof response. Nothing is positioned by a
 * hardcoded count -- a run that forked three times draws three branches --
 * so the diagram cannot drift away from the run it claims to depict.
 */

export function CopyOnWriteDiagram({ proof }: { proof: CopyOnWriteProof }) {
  const forks = Math.min(proof.forks_created, 6);
  const H = 44 + forks * 30;

  return (
    <figure className="mt-3 rounded-[4px] border border-hairline bg-surface-2 p-3">
      <svg viewBox={`0 0 420 ${H}`} className="block h-auto w-full" role="img"
        aria-label={`Trunk with ${proof.parent_rows} rows forked ${proof.forks_created} times, adding ${proof.memory_rows_added_by_forking} memory rows`}>
        <rect x={8} y={12} width={120} height={30} rx={4}
          fill="var(--color-accent-soft)" stroke="var(--color-accent)" strokeWidth={1} />
        <text x={68} y={26} textAnchor="middle" fontSize={10.5} fill="var(--color-ink)">trunk</text>
        <text x={68} y={37} textAnchor="middle" fontSize={9} fontFamily="monospace"
          fill="var(--color-ink-2)">{proof.parent_rows} rows</text>

        {Array.from({ length: forks }, (_, i) => {
          const y = 26 + (i + 1) * 30;
          return (
            <g key={i}>
              <path d={`M 128 27 C 190 27 190 ${y} 250 ${y}`} fill="none"
                stroke="var(--color-hairline-strong)" strokeWidth={1} strokeDasharray="3 3" />
              <rect x={250} y={y - 12} width={150} height={24} rx={4}
                fill="var(--color-surface)" stroke="var(--color-hairline-strong)" strokeWidth={1} />
              <text x={260} y={y + 3} fontSize={9.5} fill="var(--color-ink-2)">
                fork {i + 1} · inherits {proof.parent_rows}
              </text>
            </g>
          );
        })}
        <text x={190} y={H - 4} textAnchor="middle" fontSize={9} fill="var(--color-muted)">
          dashed = inherited, never copied
        </text>
      </svg>
      <figcaption className="mt-2 text-[12px] leading-relaxed text-ink-2">
        {proof.forks_created} forks each see {proof.parent_rows} rows.
        Memory rows written by forking:{" "}
        <span className={`font-mono font-semibold ${
          proof.memory_rows_added_by_forking === 0 ? "text-success" : "text-error"}`}>
          {proof.memory_rows_added_by_forking}
        </span>
        .
      </figcaption>
    </figure>
  );
}

export function IsolationDiagram({ proof }: { proof: IsolationProof }) {
  return (
    <figure className="mt-3 rounded-[4px] border border-hairline bg-surface-2 p-3">
      <svg viewBox="0 0 420 130" className="block h-auto w-full" role="img"
        aria-label={`Branch A returned ${proof.hits_from_a} hits for the secret; branch B returned ${proof.hits_from_b}`}>
        <rect x={8} y={10} width={170} height={46} rx={4}
          fill="var(--color-surface)" stroke="var(--color-hairline-strong)" />
        <text x={18} y={26} fontSize={10} fill="var(--color-ink)">Branch A — wrote it</text>
        <text x={18} y={44} fontSize={11} fontFamily="monospace"
          fill={proof.hits_from_a > 0 ? "var(--color-success)" : "var(--color-error)"}>
          {proof.hits_from_a} hit{proof.hits_from_a === 1 ? "" : "s"}
        </text>

        <rect x={242} y={10} width={170} height={46} rx={4}
          fill="var(--color-surface)" stroke="var(--color-hairline-strong)" />
        <text x={252} y={26} fontSize={10} fill="var(--color-ink)">Branch B — sibling</text>
        <text x={252} y={44} fontSize={11} fontFamily="monospace"
          fill={proof.hits_from_b === 0 ? "var(--color-success)" : "var(--color-error)"}>
          {proof.hits_from_b} hits
        </text>

        {/* The severed edge is the claim: B's query never reaches A's row. */}
        <path d="M 178 33 L 242 33" stroke="var(--color-hairline-strong)"
          strokeWidth={1} strokeDasharray="4 4" />
        <g stroke="var(--color-error)" strokeWidth={1.6} strokeLinecap="round">
          <path d="M 204 26 L 216 40" />
          <path d="M 216 26 L 204 40" />
        </g>

        <text x={210} y={78} textAnchor="middle" fontSize={9.5} fill="var(--color-muted)">
          identical query · identical vector index
        </text>
        <text x={210} y={96} textAnchor="middle" fontSize={10} fill="var(--color-ink-2)">
          lineage excludes the row before ranking runs
        </text>
        <text x={210} y={116} textAnchor="middle" fontSize={9} fontFamily="monospace"
          fill="var(--color-muted)">
          {proof.secret.slice(0, 46)}
        </text>
      </svg>
      <figcaption className="mt-2 text-[12px] leading-relaxed text-ink-2">
        The sibling is not ranked lower — it is not a candidate. Filtering happens at the index,
        on the <span className="font-mono">branch_id</span> prefix column.
      </figcaption>
    </figure>
  );
}
