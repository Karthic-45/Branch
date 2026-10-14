/**
 * One loading language for every page that reads real data.
 *
 * Before this, a page showed its *empty* state while its request was still in
 * flight -- "No Deployments Recorded" for the second it took CockroachDB to
 * answer -- which reads as a fact about the project rather than a wait. These
 * placeholders are shaped like the content they stand in for and name the
 * source being read, so a slow answer looks like a wait, not like "nothing".
 *
 * Keyframes are injected inline: the dev server's Tailwind does not always
 * generate new arbitrary animation classes, and a loader that silently fails
 * to animate looks exactly like a hung page.
 */

const KEYFRAMES = `
@keyframes bm-shimmer { 0% { background-position: -400px 0 } 100% { background-position: 400px 0 } }
@keyframes bm-progress { 0% { transform: translateX(-100%) } 100% { transform: translateX(350%) } }
`;

function LoadingStyles() {
  return <style>{KEYFRAMES}</style>;
}

/** A shimmering placeholder bar. */
export function Shimmer({ w = "100%", h = 12, className = "", round = 6 }: {
  w?: string | number; h?: number; className?: string; round?: number;
}) {
  return (
    <>
      <LoadingStyles />
      <span
        aria-hidden
        className={`block bg-slate-200/80 dark:bg-slate-800 ${className}`}
        style={{
          width: w,
          height: h,
          borderRadius: round,
          backgroundImage:
            "linear-gradient(90deg, rgba(255,255,255,0) 0, rgba(255,255,255,0.55) 50%, rgba(255,255,255,0) 100%)",
          backgroundSize: "400px 100%",
          backgroundRepeat: "no-repeat",
          animation: "bm-shimmer 1.3s ease-in-out infinite",
        }}
      />
    </>
  );
}

/** Thin indeterminate bar pinned under the header while any page data loads. */
export function TopProgress({ active, label }: { active: boolean; label?: string }) {
  if (!active) return null;
  return (
    <div className="relative h-0.5 w-full overflow-hidden bg-indigo-100 dark:bg-indigo-950" role="progressbar" aria-label={label ?? "Loading"}>
      <LoadingStyles />
      <div
        className="absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-indigo-500 via-blue-500 to-emerald-400"
        style={{ animation: "bm-progress 1.1s ease-in-out infinite" }}
      />
    </div>
  );
}

/** "Reading <what> from <source>…" with a spinner dot -- the one line of text a loader shows. */
export function LoadingCaption({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 text-[11.5px] font-medium text-slate-500 dark:text-slate-400" aria-live="polite">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-70" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-500" />
      </span>
      {text}
    </div>
  );
}

/** Placeholder rows for a table body. */
export function TableRowsSkeleton({ cols, rows = 6 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} aria-hidden>
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-3 py-3">
              <Shimmer w={`${c === 1 ? 85 : 55 + ((r + c) % 3) * 12}%`} h={11} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

const CARD = "rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] shadow-xs";

/**
 * A whole-page placeholder.
 * - `table`: header + KPI strip + a table (Deployments, Audit)
 * - `split`: list on the left, detail on the right (Decisions)
 * - `cards`: a grid of cards (Branches, Overview)
 */
export function PageSkeleton({ variant, caption }: { variant: "table" | "split" | "cards"; caption: string }) {
  return (
    <div className="w-full space-y-5 p-5 sm:p-6 lg:p-7" aria-busy="true">
      <div className="space-y-2">
        <Shimmer w={220} h={20} />
        <LoadingCaption text={caption} />
      </div>

      {variant === "split" && (
        <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[320px_1fr]">
          <div className={`${CARD} space-y-3 p-4`}>
            <Shimmer w={140} h={14} />
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="space-y-2 rounded-xl border border-slate-100 p-3 dark:border-slate-800">
                <Shimmer w="80%" h={11} />
                <Shimmer w="45%" h={9} />
              </div>
            ))}
          </div>
          <div className={`${CARD} space-y-5 p-6`}>
            <Shimmer w="50%" h={22} />
            <Shimmer w="70%" h={12} />
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="space-y-2 rounded-xl border border-slate-100 p-3.5 dark:border-slate-800">
                  <Shimmer w="60%" h={9} />
                  <Shimmer w="40%" h={18} />
                </div>
              ))}
            </div>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="space-y-1.5">
                <Shimmer w={`${60 + (i % 3) * 12}%`} h={10} />
                <Shimmer h={7} round={4} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === "table" && (
        <div className={`${CARD} space-y-5 p-6`}>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="space-y-2 rounded-xl border border-slate-100 p-3.5 dark:border-slate-800">
                <Shimmer w="55%" h={9} />
                <Shimmer w="35%" h={18} />
              </div>
            ))}
          </div>
          <div className="overflow-hidden rounded-xl border border-slate-100 dark:border-slate-800">
            <div className="h-9 bg-slate-50 dark:bg-slate-900/60" />
            <table className="w-full">
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                <TableRowsSkeleton cols={6} rows={6} />
              </tbody>
            </table>
          </div>
        </div>
      )}

      {variant === "cards" && (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className={`${CARD} space-y-2.5 p-4`}>
                <Shimmer w="50%" h={9} />
                <Shimmer w="35%" h={22} />
                <Shimmer w="70%" h={8} />
              </div>
            ))}
          </div>
          <div className={`${CARD} p-5`}>
            <Shimmer w={180} h={14} className="mb-4" />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="space-y-3 rounded-xl border border-slate-100 p-4 dark:border-slate-800">
                  <div className="flex justify-between"><Shimmer w="35%" h={10} /><Shimmer w="25%" h={14} round={10} /></div>
                  <Shimmer w="85%" h={11} />
                  <Shimmer w="60%" h={9} />
                  <Shimmer h={34} round={8} />
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
