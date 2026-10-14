import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { motion } from "framer-motion";
import { Search, Plus, Minus, Maximize2 } from "lucide-react";
import type { BranchNode, Stage } from "../lib/types";
import type { Theme } from "../hooks/useTheme";
import { pct, truncate } from "../lib/stages";

interface Props {
  nodes: BranchNode[];
  branchStage: Map<string, Stage>;
  theme: Theme;
  /** Opening the inspector. Omitted on surfaces that only display the tree. */
  onSelect?: (branch: BranchNode) => void;
  selectedId?: string | null;
}

type HNode = d3.HierarchyPointNode<BranchNode>;
type HLink = d3.HierarchyPointLink<BranchNode>;

const COL = 250;
const ROW = 108;
// Wrapped across up to two lines rather than cut at one -- a hypothesis is a
// full sentence (85+ characters is typical), and hard-truncating at ~30 chars
// hid most of it even once there was visible empty space to the right of the
// label to hold more. Two lines of 40 covers essentially every real one seen
// so far; only a genuinely longer sentence still falls back to an ellipsis.
const LABEL_CHARS_PER_LINE = 40;
const LABEL_MAX_LINES = 2;
const LABEL_X = 30;
const PAD = { top: 52, right: 340, bottom: 40, left: 64 };

/** Greedy word-wrap, not a hard character cut -- breaks only on whitespace,
 * so words are never split mid-word. Whatever doesn't fit in maxLines gets
 * ellipsis-truncated on the last line rather than silently dropped. */
function wrapLabel(text: string, maxCharsPerLine: number, maxLines: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = "";
  let i = 0;
  while (i < words.length && lines.length < maxLines) {
    const word = words[i];
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length <= maxCharsPerLine || !current) {
      current = candidate;
      i++;
    } else {
      lines.push(current);
      current = "";
    }
  }
  if (current) lines.push(current);
  if (i < words.length && lines.length) {
    const last = lines.length - 1;
    lines[last] = truncate(lines[last], maxCharsPerLine - 1);
  }
  return lines;
}

// Mirrors index.css's @theme, light and dark -- SVG fill/stroke attributes
// can't reach CSS custom properties the way className can, so these have to
// be kept in sync by hand against both theme blocks.
const PALETTE = {
  light: {
    // Mirrors index.css by hand: SVG attributes cannot read CSS custom
    // properties, so these must be kept in step with the tokens. Semantic,
    // not decorative -- accent for the trunk and anything in flight,
    // success for what survived, muted for what was abandoned.
    violet: "#2563eb",
    emerald: "#15803d",
    fuchsia: "#0369a1",
    amber: "#b45309",
    muted: "#8b949e",
    ink: "#17191c",
    nodeSurface: "#ffffff",
    dotGrid: "#e2e5e9",
    linkAbandoned: "#e2e5e9",
    linkIdle: "#d0d5db",
    labelAbandoned: "#8b949e",
    track: "#e8ebee",
    winnerGlow: "#15803d",
  },
  dark: {
    violet: "#4d86f7",
    emerald: "#34a95d",
    fuchsia: "#2b95d1",
    amber: "#d98324",
    muted: "#6b7280",
    ink: "#f3f4f6",
    nodeSurface: "#171a21",
    dotGrid: "#2a303a",
    linkAbandoned: "#2a303a",
    linkIdle: "#3a4250",
    labelAbandoned: "#6b7280",
    track: "#1d222b",
    winnerGlow: "#34a95d",
  },
} as const;

export function BranchTree({ nodes, branchStage, theme, onSelect, selectedId }: Props) {
  const c = PALETTE[theme];
  const [tip, setTip] = useState<{ node: BranchNode; x: number; y: number } | null>(null);
  const [query, setQuery] = useState("");
  // A callback ref, not useRef: it fires exactly when the <svg> mounts, which
  // is the only moment the zoom behaviour should attach. Keying the effect on
  // `layout` instead re-initialised d3.zoom on every SSE tick -- roughly once
  // a second -- which reset the transform and interrupted the toolbar's own
  // transitions mid-flight, so wheel zoom worked and the buttons did not.
  const [svgEl, setSvgEl] = useState<SVGSVGElement | null>(null);
  const svgRef = useCallback((node: SVGSVGElement | null) => setSvgEl(node), []);
  const zoomLayer = useRef<SVGGElement | null>(null);
  const zoomBehavior = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const [scale, setScale] = useState(1);

  const zoomBy = useCallback((factor: number) => {
    if (!svgEl || !zoomBehavior.current) return;
    d3.select(svgEl).call(zoomBehavior.current.scaleBy, factor);
  }, [svgEl]);

  const fit = useCallback(() => {
    if (!svgEl || !zoomBehavior.current) return;
    d3.select(svgEl).call(zoomBehavior.current.transform, d3.zoomIdentity);
  }, [svgEl]);

  const layout = useMemo(() => {
    if (!nodes.length) return null;
    let root: d3.HierarchyNode<BranchNode>;
    try {
      root = d3.stratify<BranchNode>().id((d) => d.id).parentId((d) => d.parent_id)(nodes);
    } catch {
      return null; // a partially-written tree can briefly have an unreachable parent
    }
    const leaves = root.leaves().length;
    const maxDepth = d3.max(root.descendants(), (d) => d.depth) ?? 0;
    const innerH = Math.max(ROW, (leaves - 1) * ROW);
    const innerW = Math.max(COL, maxDepth * COL);
    d3.tree<BranchNode>().size([innerH, innerW])(root);
    return { root: root as HNode, width: innerW + PAD.left + PAD.right, height: innerH + PAD.top + PAD.bottom };
  }, [nodes]);

  // d3.zoom owns the transform; React only reads the scale back out for the
  // readout. Driving the transform from React state instead would re-render
  // the whole tree on every wheel tick.
  useEffect(() => {
    const layer = zoomLayer.current;
    if (!svgEl || !layer) return;
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 4])
      .on("zoom", (event) => {
        layer.setAttribute("transform", event.transform.toString());
        setScale(event.transform.k);
      });
    zoomBehavior.current = zoom;
    d3.select(svgEl).call(zoom);
    return () => {
      d3.select(svgEl).on(".zoom", null);
      zoomBehavior.current = null;
    };
  }, [svgEl]);

  // Branch search dims rather than hides: a filtered tree with edges removed
  // stops being a lineage, and lineage is the whole point of the diagram.
  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!needle) return null;
    return new Set(
      nodes
        .filter((n) => (n.hypothesis ?? "").toLowerCase().includes(needle) || n.id.includes(needle))
        .map((n) => n.id),
    );
  }, [nodes, needle]);

  if (!layout) {
    return (
      <div className="px-6 py-16 text-center">
        <p className="text-[13.5px] text-muted">
          Enter a root branch id above to watch its tree build itself live.
        </p>
        <code className="mt-3 inline-block rounded-lg bg-surface-2 px-2.5 py-1 font-mono text-xs text-ink-2">
          ?root=&lt;uuid&gt;
        </code>
      </div>
    );
  }

  const { root, width, height } = layout;
  const winnerId = nodes.find((n) => n.status === "merged")?.id;
  const linkPath = d3.linkHorizontal<HLink, HNode>().x((d) => d.y).y((d) => d.x);

  return (
    <div className="relative rounded-[4px] border border-hairline">
      <div className="flex flex-wrap items-center gap-2 border-b border-hairline px-2 py-1.5">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5
            -translate-y-1/2 text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a branch"
            aria-label="Find a branch"
            className="w-48 rounded-[4px] border border-hairline-strong bg-surface py-1 pl-7 pr-2
              text-[12px] text-ink placeholder:text-muted focus:border-accent focus:outline-none"
          />
        </div>
        {matches && (
          <span className="font-mono text-[11px] text-muted" role="status">
            {matches.size} of {nodes.length} match
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <ZoomButton label="Zoom out" onClick={() => zoomBy(0.8)}><Minus className="h-3.5 w-3.5" /></ZoomButton>
          <span className="w-11 text-center font-mono text-[11px] text-muted tabular">
            {Math.round(scale * 100)}%
          </span>
          <ZoomButton label="Zoom in" onClick={() => zoomBy(1.25)}><Plus className="h-3.5 w-3.5" /></ZoomButton>
          <ZoomButton label="Fit to screen" onClick={fit}><Maximize2 className="h-3.5 w-3.5" /></ZoomButton>
        </div>
      </div>

      <div className="overflow-hidden">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          className="block h-auto w-full cursor-grab touch-none active:cursor-grabbing"
          style={{ minWidth: Math.min(width, 640) }}
          role="img"
          aria-label={`Branch tree: ${nodes.length} branches`}
        >
          <defs>
            <pattern id="tree-canvas-grid" width="26" height="26" patternUnits="userSpaceOnUse">
              <circle cx="1.4" cy="1.4" r="1.4" fill={c.dotGrid} />
            </pattern>
          </defs>
          <rect x={0} y={0} width={width} height={height} fill="url(#tree-canvas-grid)" />

          <g ref={zoomLayer}>
            <g transform={`translate(${PAD.left},${PAD.top})`}>
              {root.links().map((link) => (
                <Link
                  key={link.target.data.id}
                  d={linkPath(link) ?? ""}
                  won={link.target.data.id === winnerId}
                  abandoned={link.target.data.status === "abandoned"}
                  c={c}
                />
              ))}
              {root.descendants().map((d) => (
                <Node
                  key={d.data.id}
                  d={d}
                  stage={branchStage.get(d.data.id)}
                  isWinner={d.data.id === winnerId}
                  dimmed={matches !== null && !matches.has(d.data.id)}
                  selected={selectedId === d.data.id}
                  onHover={(e) => setTip({ node: d.data, x: e.clientX, y: e.clientY })}
                  onLeave={() => setTip(null)}
                  onSelect={onSelect}
                  c={c}
                />
              ))}
            </g>
          </g>
        </svg>
      </div>
      {tip && <Tooltip node={tip.node} x={tip.x} y={tip.y} />}
    </div>
  );
}

type Palette = (typeof PALETTE)[Theme];

function Link({ d, won, abandoned, c }: { d: string; won: boolean; abandoned: boolean; c: Palette }) {
  return (
    <g>
      {/* Always drawn at full opacity. The connection is structural truth --
          it must never depend on an animation having completed. */}
      <path
        d={d}
        fill="none"
        strokeWidth={won ? 3 : 1.75}
        stroke={won ? c.emerald : abandoned ? c.linkAbandoned : c.linkIdle}
        style={{ transition: "stroke 600ms ease, stroke-width 600ms ease" }}
      />
      {won && (
        <motion.path
          d={d} fill="none" strokeWidth={3} strokeLinecap="round" stroke={c.winnerGlow}
          strokeDasharray="6 26"
          animate={{ strokeDashoffset: [0, -32] }}
          transition={{ repeat: Infinity, duration: 1.1, ease: "linear" }}
          style={{ filter: `drop-shadow(0 0 5px ${c.emerald})` }}
        />
      )}
    </g>
  );
}

function Node({
  d, stage, isWinner, dimmed, selected, onHover, onLeave, onSelect, c,
}: {
  d: HNode; stage: Stage | undefined; isWinner: boolean;
  dimmed: boolean; selected: boolean;
  onHover: (e: React.MouseEvent) => void; onLeave: () => void;
  onSelect?: (branch: BranchNode) => void; c: Palette;
}) {
  const node = d.data;
  const isTrunk = d.depth === 0;
  const abandoned = node.status === "abandoned";
  const exploring = node.status === "exploring";
  const thinking = stage === "exploring" || stage === "explored";
  const building = stage === "evaluating" || stage === "uploading" || stage === "building";
  const p = pct(node.score);
  const r = isTrunk ? 13 : 10;

  const fill = isTrunk ? c.violet : isWinner ? c.emerald : abandoned ? "transparent" : c.nodeSurface;
  const stroke = isTrunk ? c.violet : isWinner ? c.emerald : abandoned ? c.muted : c.violet;

  return (
    <g
      transform={`translate(${d.y},${d.x})`}
      onMouseMove={onHover}
      onMouseLeave={onLeave}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={`${node.hypothesis ?? node.id.slice(0, 8)} — ${node.status}`}
      onClick={() => onSelect?.(node)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(node);
        }
      }}
      style={{ opacity: dimmed ? 0.22 : 1, cursor: onSelect ? "pointer" : "default" }}
      className="outline-none focus-visible:[&>circle:last-of-type]:stroke-[3]"
    >
      {/* Selection is a ring, not a colour change -- status already owns
          colour here, and overloading it would make a selected abandoned
          branch unreadable. */}
      {selected && (
        <circle r={r + 11} fill="none" stroke={c.violet} strokeWidth={1.5} strokeDasharray="3 3" />
      )}
      {isWinner && (
        <motion.circle
          r={r + 8} fill="none" stroke={c.emerald} strokeWidth={1.5}
          animate={{ opacity: [0.7, 0.15, 0.7], scale: [1, 1.12, 1] }}
          transition={{ repeat: Infinity, duration: 2.2, ease: "easeInOut" }}
        />
      )}
      {exploring && (
        <motion.circle
          r={r} fill="none" stroke={c.violet} strokeWidth={2}
          animate={{ r: [r, r + 12], opacity: [0.6, 0] }}
          transition={{ repeat: Infinity, duration: 1.8, ease: "easeOut" }}
        />
      )}

      <circle
        r={r}
        fill={fill}
        stroke={stroke}
        strokeWidth={2.25}
        strokeDasharray={abandoned ? "3 3" : undefined}
        opacity={abandoned ? 0.55 : 1}
        style={{
          transition: "fill 600ms ease, stroke 600ms ease, opacity 600ms ease",
          filter: isWinner ? `drop-shadow(0 0 8px ${c.emerald})` : isTrunk ? `drop-shadow(0 0 8px ${c.violet})` : undefined,
        }}
      />

      {(thinking || building) && <ActivityBadge kind={thinking ? "thinking" : "building"} r={r} c={c} />}

      {isTrunk ? (
        <text x={0} y={-24} textAnchor="middle" fontSize={13} fontWeight={650} fill={c.ink}>
          main
        </text>
      ) : (
        // Wrapped across up to two lines rather than one hard-truncated one --
        // see wrapLabel above for why. Horizontally clear of the activity
        // badge above the node (badge spans roughly x -10..10, labels start
        // at LABEL_X=30), so the two never collide even when both are present.
        wrapLabel(node.hypothesis ?? "branch", LABEL_CHARS_PER_LINE, LABEL_MAX_LINES).map((line, i) => (
          <text
            key={i}
            x={LABEL_X}
            y={-34 + i * 15}
            fontSize={13}
            fontWeight={650}
            fill={abandoned ? c.labelAbandoned : c.ink}
            style={{ transition: "fill 600ms ease" }}
          >
            {line}
          </text>
        ))
      )}
      <text x={LABEL_X} y={10} fontSize={10} className="font-mono" fill={c.muted}>
        {isTrunk ? "" : p !== null ? `${node.status} · ${p}%` : (stage ?? node.status)}
      </text>

      {!isTrunk && p !== null && (
        <>
          <rect x={LABEL_X} y={18} width={116} height={6} rx={3} fill={c.track} />
          <motion.rect
            x={LABEL_X} y={18} height={6} rx={3}
            initial={{ width: 0 }}
            animate={{ width: Math.max(3, (116 * p) / 100) }}
            transition={{ duration: 0.7, ease: [0.4, 0, 0.2, 1] }}
            fill={isWinner ? c.emerald : abandoned ? c.muted : c.violet}
          />
        </>
      )}
    </g>
  );
}

/** The same two motifs the pipeline rail uses at run level, repeated here at
 * branch level: bouncing dots = a model is reasoning, a chasing ring =
 * CodeBuild is running. A viewer learns the vocabulary once. */
function ActivityBadge({ kind, r, c }: { kind: "thinking" | "building"; r: number; c: Palette }) {
  // Centred directly above the node, never off to the side. The label sits at
  // LABEL_X to the right, so keeping the badge on the node's own vertical axis
  // makes an overlap geometrically impossible rather than merely unlikely --
  // an earlier version placed it up-and-right and collided with long labels.
  const cx = 0, cy = -r - 17;
  const color = kind === "thinking" ? c.fuchsia : c.amber;
  return (
    <motion.g initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.25 }}>
      <circle cx={cx} cy={cy} r={10} fill={c.nodeSurface} stroke={color} strokeWidth={1.5}
              style={{ filter: `drop-shadow(0 0 6px ${color})` }} />
      {kind === "thinking" ? (
        [-3.4, 0, 3.4].map((dx, i) => (
          <motion.circle
            key={dx} cx={cx + dx} cy={cy} r={1.5} fill={color}
            animate={{ cy: [cy, cy - 3, cy] }}
            transition={{ repeat: Infinity, duration: 0.9, delay: i * 0.15 }}
          />
        ))
      ) : (
        <motion.circle
          cx={cx} cy={cy} r={5.5} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round"
          strokeDasharray="7 27.5"
          animate={{ strokeDashoffset: [0, -34.5] }}
          transition={{ repeat: Infinity, duration: 0.9, ease: "linear" }}
        />
      )}
    </motion.g>
  );
}

function Tooltip({ node, x, y }: { node: BranchNode; x: number; y: number }) {
  const p = pct(node.score);
  return (
    <div className="card pointer-events-none fixed z-40 max-w-80 rounded-xl px-3.5 py-3 text-[12.5px]"
         style={{ left: x + 16, top: y + 16 }}>
      <div className="mb-1.5 font-semibold text-ink">{node.hypothesis ?? "main (trunk)"}</div>
      <Row k="state" v={node.status} />
      {p !== null && <Row k="score" v={`${p}%`} />}
      <Row k="depth" v={String(node.depth)} />
      <Row k="branch" v={node.id.slice(0, 8)} />
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4 text-ink-2">
      <span>{k}</span>
      <span className="font-mono text-ink">{v}</span>
    </div>
  );
}


/** Toolbar control. A real <button> with a label, so the tree stays operable
 * from the keyboard -- wheel-zoom alone is not an accessible control. */
function ZoomButton({
  label, onClick, children,
}: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="flex h-6 w-6 items-center justify-center rounded-[4px] border border-hairline-strong
        text-ink-2 transition-colors hover:border-accent hover:text-accent
        focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      {children}
    </button>
  );
}
