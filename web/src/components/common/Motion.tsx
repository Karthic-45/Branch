import { useEffect, useRef, useState } from "react";

/* Small motion helpers shared by pages. Values shown are always the real
   ones -- animation only changes how they arrive, never what they are. */

const reduced = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Counts from the previous value to `value` over `ms`. Renders the final
 *  value immediately when the user prefers reduced motion. */
export function CountUp({
  value, ms = 900, format = (n: number) => Math.round(n).toLocaleString(), className,
}: {
  value: number;
  ms?: number;
  format?: (n: number) => string;
  className?: string;
}) {
  const [shown, setShown] = useState(reduced() ? value : 0);
  const from = useRef(0);

  useEffect(() => {
    if (reduced()) { setShown(value); return; }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / ms);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(a + (value - a) * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = value;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);

  return <span className={className}>{format(shown)}</span>;
}

/** A ring that sweeps to `pct` (0..1). */
export function Ring({
  pct, size = 96, stroke = 10, color = "#10b981", track = "currentColor", children,
}: {
  pct: number; size?: number; stroke?: number; color?: string; track?: string; children?: React.ReactNode;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const [p, setP] = useState(reduced() ? pct : 0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setP(pct));
    return () => cancelAnimationFrame(id);
  }, [pct]);
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} className="text-slate-100 dark:text-slate-800" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - Math.max(0, Math.min(1, p)))}
          style={{ transition: "stroke-dashoffset 1s cubic-bezier(0.22, 1, 0.36, 1)" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}

/** Horizontal bar that grows to `pct` (0..1) on mount. */
export function GrowBar({ pct, className = "bg-indigo-500", height = 8 }: { pct: number; className?: string; height?: number }) {
  const [w, setW] = useState(reduced() ? pct : 0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setW(pct));
    return () => cancelAnimationFrame(id);
  }, [pct]);
  return (
    <div className="w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" style={{ height }}>
      <div
        className={`h-full rounded-full ${className}`}
        style={{ width: `${Math.max(0, Math.min(1, w)) * 100}%`, transition: "width 0.9s cubic-bezier(0.22, 1, 0.36, 1)" }}
      />
    </div>
  );
}
