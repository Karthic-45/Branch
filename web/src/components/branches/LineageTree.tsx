import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { GitBranch, Minus, Plus, Maximize2, Trophy } from "lucide-react";
import type { BranchNode, Stage } from "../../lib/types";

/* Top-down lineage tree: the trunk at the top, each fork a card below its
   parent, connected by curves that draw in on load. Layout is a simple tidy
   tree -- every leaf gets one column, every parent centres over its
   children -- which reads well for the shape this system produces (one trunk,
   a fan of sibling strategies, occasionally a deeper replay). */

const CARD_W = 184;
const CARD_H = 104;
const GAP_X = 18;
const GAP_Y = 64;
const PAD = 24;

type Kind = "merged" | "exploring" | "evaluated" | "abandoned" | "failed" | "root";

const KIND: Record<Kind, { label: string; ring: string; bar: string; chip: string; line: string }> = {
  root: { label: "Trunk", ring: "border-indigo-300 dark:border-indigo-700", bar: "bg-indigo-500", chip: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300", line: "#6366f1" },
  merged: { label: "Merged", ring: "border-emerald-300 dark:border-emerald-700", bar: "bg-emerald-500", chip: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300", line: "#10b981" },
  exploring: { label: "Exploring", ring: "border-blue-300 dark:border-blue-700", bar: "bg-blue-500", chip: "bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300", line: "#3b82f6" },
  evaluated: { label: "Evaluated", ring: "border-purple-300 dark:border-purple-700", bar: "bg-purple-500", chip: "bg-purple-100 text-purple-700 dark:bg-purple-950/60 dark:text-purple-300", line: "#a855f7" },
  abandoned: { label: "Abandoned", ring: "border-amber-200 dark:border-amber-800", bar: "bg-amber-500", chip: "bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300", line: "#f59e0b" },
  failed: { label: "Failed", ring: "border-rose-200 dark:border-rose-800", bar: "bg-rose-500", chip: "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300", line: "#f43f5e" },
};

export function kindOf(n: BranchNode): Kind {
  if (n.parent_id === null) return "root";
  if (n.status === "merged") return "merged";
  if (n.status === "exploring") return n.score !== null ? "evaluated" : "exploring";
  if (!n.total && n.reason && n.reason !== "ok") return "failed";
  return "abandoned";
}

interface Placed { node: BranchNode; x: number; y: number; kind: Kind }

function layout(nodes: BranchNode[]): { placed: Placed[]; width: number; height: number } {
  const byParent = new Map<string | null, BranchNode[]>();
  for (const n of nodes) {
    const k = n.parent_id;
    if (!byParent.has(k)) byParent.set(k, []);
    byParent.get(k)!.push(n);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => (Date.parse(a.created_at ?? "") || 0) - (Date.parse(b.created_at ?? "") || 0));
  }
  const ids = new Set(nodes.map((n) => n.id));
  const roots = nodes.filter((n) => n.parent_id === null || !ids.has(n.parent_id));
  const placed: Placed[] = [];
  let col = 0;
  let maxDepth = 0;
  const place = (n: BranchNode, depth: number): number => {
    maxDepth = Math.max(maxDepth, depth);
    const kids = byParent.get(n.id) ?? [];
    let cx: number;
    if (!kids.length) {
      cx = col * (CARD_W + GAP_X);
      col += 1;
    } else {
      const xs = kids.map((k) => place(k, depth + 1));
      cx = (xs[0] + xs[xs.length - 1]) / 2;
    }
    placed.push({ node: n, x: cx, y: depth * (CARD_H + GAP_Y), kind: kindOf(n) });
    return cx;
  };
  roots.forEach((r) => place(r, 0));
  const width = Math.max(1, col) * (CARD_W + GAP_X) - GAP_X;
  const height = (maxDepth + 1) * (CARD_H + GAP_Y) - GAP_Y;
  return { placed, width, height };
}

function label(n: BranchNode): string {
  if (n.parent_id === null) return n.hypothesis && n.hypothesis !== "main" ? n.hypothesis : "main";
  return n.hypothesis ?? `branch ${n.id.slice(0, 8)}`;
}

export function LineageTree({
  nodes, branchStage, onSelect,
}: {
  nodes: BranchNode[];
  branchStage: Map<string, Stage>;
  onSelect?: (n: BranchNode) => void;
}) {
  const { placed, width, height } = useMemo(() => layout(nodes), [nodes]);
  const box = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(1);
  const [zoom, setZoom] = useState<number | null>(null); // null = fit to width
  // Fit the whole tree to the available width by default; the buttons
  // override it, and "reset" returns to fit.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setFit(Math.min(1, Math.max(0.45, (el.clientWidth - 4) / (width + PAD * 2))));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [width]);
  const z = zoom ?? fit;
  const pos = useMemo(() => new Map(placed.map((p) => [p.node.id, p])), [placed]);
  const childCount = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of nodes) if (n.parent_id) m.set(n.parent_id, (m.get(n.parent_id) ?? 0) + 1);
    return m;
  }, [nodes]);

  if (!nodes.length) {
    return <div className="flex h-60 items-center justify-center text-xs text-slate-400">No branches yet.</div>;
  }

  const W = width + PAD * 2;
  const H = height + PAD * 2;

  return (
    <div className="relative">
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-lg border border-slate-200 bg-white/90 p-0.5 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/90">
        <button aria-label="Zoom out" onClick={() => setZoom(Math.max(0.45, +(z - 0.1).toFixed(2)))} className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"><Minus className="h-3.5 w-3.5" /></button>
        <span className="w-10 text-center font-mono text-[10.5px] text-slate-500">{Math.round(z * 100)}%</span>
        <button aria-label="Zoom in" onClick={() => setZoom(Math.min(1.6, +(z + 0.1).toFixed(2)))} className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"><Plus className="h-3.5 w-3.5" /></button>
        <button aria-label="Fit to width" onClick={() => setZoom(null)} className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"><Maximize2 className="h-3.5 w-3.5" /></button>
      </div>

      <div
        ref={box}
        className="overflow-auto rounded-xl"
        style={{
          backgroundImage: "radial-gradient(circle, rgb(148 163 184 / 0.25) 1px, transparent 1px)",
          backgroundSize: "18px 18px",
        }}
      >
        <div className="mx-auto" style={{ width: W * z, height: H * z }}>
          <div className="relative origin-top-left" style={{ width: W, height: H, transform: `scale(${z})` }}>
            <svg width={W} height={H} className="absolute inset-0">
              {placed.filter((p) => p.node.parent_id && pos.has(p.node.parent_id)).map((p, i) => {
                const parent = pos.get(p.node.parent_id!)!;
                const x1 = PAD + parent.x + CARD_W / 2;
                const y1 = PAD + parent.y + CARD_H;
                const x2 = PAD + p.x + CARD_W / 2;
                const y2 = PAD + p.y;
                const my = (y1 + y2) / 2;
                const active = p.kind === "merged";
                return (
                  <path
                    key={p.node.id}
                    d={`M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`}
                    fill="none"
                    stroke={KIND[p.kind].line}
                    strokeOpacity={active ? 0.9 : 0.45}
                    strokeWidth={active ? 2.5 : 1.6}
                    className="bm-draw"
                    style={{ animationDelay: `${0.05 * i}s` }}
                  />
                );
              })}
            </svg>

            {placed.map((p, i) => {
              const n = p.node;
              const k = KIND[p.kind];
              const stage = branchStage.get(n.id);
              const live = n.status === "exploring" && stage && !["scored", "merged", "abandoned"].includes(stage);
              const kids = childCount.get(n.id) ?? 0;
              const pct = n.score !== null ? n.score : null;
              return (
                <motion.button
                  key={n.id}
                  type="button"
                  onClick={() => onSelect?.(n)}
                  initial={{ opacity: 0, y: 8, scale: 0.96 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  transition={{ delay: 0.04 * i, duration: 0.3 }}
                  whileHover={{ y: -3 }}
                  className={`absolute flex flex-col rounded-xl border-2 bg-white p-2.5 text-left shadow-sm transition-shadow hover:shadow-md dark:bg-[#151924] ${k.ring} ${live ? "bm-glow" : ""}`}
                  style={{ left: PAD + p.x, top: PAD + p.y, width: CARD_W, height: CARD_H }}
                  title={label(n)}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="flex items-center gap-1.5 font-mono text-[10.5px] font-bold text-slate-500">
                      <GitBranch className="h-3 w-3" />
                      {n.parent_id === null ? "main" : n.id.slice(0, 8)}
                    </span>
                    <span className={`flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-bold ${k.chip}`}>
                      {p.kind === "merged" && <Trophy className="h-2.5 w-2.5" />}
                      {live ? stage : k.label}
                    </span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-[11.5px] font-semibold leading-snug text-slate-800 dark:text-slate-100">
                    {label(n)}
                  </div>
                  <div className="mt-auto">
                    {n.parent_id === null ? (
                      <div className="text-[10.5px] text-slate-500">{kids} branch{kids === 1 ? "" : "es"}</div>
                    ) : (
                      <>
                        <div className="mb-1 flex justify-between font-mono text-[10px] text-slate-500">
                          <span>{n.total ? `${n.passed}/${n.total} tests` : "no tests"}</span>
                          <span className="font-bold text-slate-700 dark:text-slate-200">{pct !== null ? `${(pct * 100).toFixed(1)}%` : "—"}</span>
                        </div>
                        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                          <motion.div
                            className={`h-full rounded-full ${k.bar}`}
                            initial={{ width: 0 }}
                            animate={{ width: `${(pct ?? 0) * 100}%` }}
                            transition={{ delay: 0.2 + 0.04 * i, duration: 0.7 }}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </motion.button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
