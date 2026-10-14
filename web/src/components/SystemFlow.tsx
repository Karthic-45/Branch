import { motion } from "framer-motion";
import { Database, Cpu, Sparkles, Box, FlaskConical } from "lucide-react";
import type { FeedEvent, Stage } from "../lib/types";
import type { Theme } from "../hooks/useTheme";
import { deriveFlow, type EdgeId, type NodeId } from "../lib/flow";

/* Fixed 1040x400 coordinate space, scaled by the viewBox. Every position here
   is authored once against that grid, so edges and boxes can never drift apart
   the way they would if boxes were HTML and edges were SVG. */

interface NodeSpec {
  id: NodeId;
  x: number; y: number; w: number; h: number;
  title: string; sub: string;
  icon: typeof Database;
}

const NODES: NodeSpec[] = [
  { id: "db", x: 24, y: 148, w: 196, h: 104, title: "CockroachDB", sub: "branches · vectors", icon: Database },
  { id: "cp", x: 380, y: 148, w: 196, h: 104, title: "Control plane", sub: "FastAPI orchestrator", icon: Cpu },
  { id: "bedrock", x: 800, y: 30, w: 206, h: 84, title: "Bedrock", sub: "Claude · Titan", icon: Sparkles },
  { id: "s3", x: 800, y: 158, w: 206, h: 84, title: "S3", sub: "patch staging", icon: Box },
  { id: "codebuild", x: 800, y: 286, w: 206, h: 84, title: "CodeBuild", sub: "real pytest suite", icon: FlaskConical },
];

interface EdgeSpec {
  id: EdgeId;
  d: string;
  label: string;
  lx: number; ly: number;
  anchor: "start" | "middle" | "end";
}

const EDGES: EdgeSpec[] = [
  { id: "db-cp", d: "M 220 180 L 380 180", label: "inherited memory", lx: 300, ly: 171, anchor: "middle" },
  { id: "cp-db", d: "M 380 220 L 220 220", label: "new belief", lx: 300, ly: 240, anchor: "middle" },
  { id: "cp-bedrock", d: "M 576 180 Q 688 180 688 126 Q 688 72 800 72", label: "prompt + context", lx: 676, ly: 116, anchor: "end" },
  { id: "cp-s3", d: "M 576 200 L 800 200", label: "patch.diff", lx: 688, ly: 191, anchor: "middle" },
  { id: "s3-cb", d: "M 903 242 L 903 286", label: "source", lx: 917, ly: 268, anchor: "start" },
  { id: "cb-cp", d: "M 800 328 Q 688 328 688 274 Q 688 220 576 220", label: "pass rate", lx: 676, ly: 300, anchor: "end" },
];

// Mirrors index.css's @theme, light and dark -- SVG attributes can't read
// Tailwind classes, so these have to be kept in sync by hand against both
// theme blocks. Position/title/icon never change across themes, only hue.
// Mirrors index.css by hand -- SVG attributes cannot read CSS custom
// properties. CockroachDB and AWS keep their vendor marks because this is the
// one diagram where identifying the vendor is the point; everything else is
// the accent. The old "glow" channel is gone with the glow itself.
const NODE_COLOR: Record<Theme, Record<NodeId, { color: string }>> = {
  light: {
    db: { color: "#a63232" },
    cp: { color: "#2563eb" },
    bedrock: { color: "#c2610a" },
    s3: { color: "#c2610a" },
    codebuild: { color: "#c2610a" },
  },
  dark: {
    db: { color: "#e06c6c" },
    cp: { color: "#6b9bfa" },
    bedrock: { color: "#e8940f" },
    s3: { color: "#e8940f" },
    codebuild: { color: "#e8940f" },
  },
};
const EDGE_COLOR: Record<Theme, Record<EdgeId, string>> = {
  light: {
    "db-cp": "#a63232", "cp-db": "#15803d", "cp-bedrock": "#c2610a",
    "cp-s3": "#c2610a", "s3-cb": "#c2610a", "cb-cp": "#15803d",
  },
  dark: {
    "db-cp": "#e06c6c", "cp-db": "#34a95d", "cp-bedrock": "#e8940f",
    "cp-s3": "#e8940f", "s3-cb": "#e8940f", "cb-cp": "#34a95d",
  },
};
const NEUTRAL: Record<Theme, {
  wire: string; labelIdle: string; nodeIdleFill: string; nodeIdleStroke: string;
  iconIdle: string; titleActive: string; titleIdle: string; sub: string;
}> = {
  light: {
    wire: "rgb(15 23 33 / 14%)", labelIdle: "rgb(15 23 33 / 38%)",
    nodeIdleFill: "#ffffff", nodeIdleStroke: "#e2e5e9",
    iconIdle: "rgb(15 23 33 / 40%)", titleActive: "#1a1d21", titleIdle: "rgb(15 23 33 / 55%)",
    sub: "rgb(15 23 33 / 45%)",
  },
  dark: {
    wire: "rgb(255 255 255 / 14%)", labelIdle: "rgb(255 255 255 / 36%)",
    nodeIdleFill: "#161a20", nodeIdleStroke: "#272c34",
    iconIdle: "rgb(255 255 255 / 38%)", titleActive: "#e8eaed", titleIdle: "rgb(255 255 255 / 52%)",
    sub: "rgb(255 255 255 / 42%)",
  },
};

const TONE_COLOR: Record<string, string> = {
  idle: "text-muted",
  reasoning: "text-accent",
  build: "text-warning",
  success: "text-success",
  danger: "text-error",
};

export function SystemFlow({
  events, branchStage, theme,
}: {
  events: FeedEvent[]; branchStage: Map<string, Stage>; theme: Theme;
}) {
  const flow = deriveFlow(events, branchStage);
  const n = NEUTRAL[theme];

  return (
    <div className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-hairline px-4 py-2.5">
        <h2 className="text-[12px] font-semibold uppercase tracking-wide text-ink-2">Live data paths</h2>
        <span className="text-[12px] text-muted">
          Every line is a real call. Dots move only while that path is actually carrying data.
        </span>
      </div>

      <div className="overflow-x-auto px-4 py-4">
        <svg viewBox="0 0 1040 400" className="block h-auto w-full min-w-[760px]" role="img"
             aria-label="Live architecture diagram showing data flowing between CockroachDB, the control plane, Bedrock, S3 and CodeBuild">
          {EDGES.map((e) => (
            <Edge key={e.id} spec={e} active={flow.edges.has(e.id)} color={EDGE_COLOR[theme][e.id]} n={n} />
          ))}
          {NODES.map((spec) => (
            <Node key={spec.id} spec={{ ...spec, ...NODE_COLOR[theme][spec.id] }} active={flow.nodes.has(spec.id)} n={n} />
          ))}
        </svg>
      </div>

      <div className="flex items-start gap-3 border-t border-hairline px-5 py-3.5">
        <span className="relative mt-1.5 flex h-2 w-2 flex-none">
          {flow.tone !== "idle" && (
            <motion.span
              className="absolute inline-flex h-full w-full rounded-full bg-current opacity-60"
              animate={{ scale: [1, 2.6], opacity: [0.6, 0] }}
              transition={{ repeat: Infinity, duration: 1.5, ease: "easeOut" }}
            />
          )}
          <span className={`relative inline-flex h-2 w-2 rounded-full bg-current ${TONE_COLOR[flow.tone]}`} />
        </span>
        <p className={`text-[13.5px] font-medium ${TONE_COLOR[flow.tone]}`}>{flow.caption}</p>
      </div>
    </div>
  );
}

type Neutral = (typeof NEUTRAL)[Theme];

function Edge({ spec, active, color, n }: { spec: EdgeSpec; active: boolean; color: string; n: Neutral }) {
  return (
    <g>
      {/* The wire itself is always drawn, at rest. Nothing about whether this
          connection *exists* depends on an animation running. */}
      <path d={spec.d} fill="none" stroke={n.wire} strokeWidth={2} strokeLinecap="round" />

      {active && (
        <>
          <path d={spec.d} fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" opacity={0.28} />
          {/* Packets. A repeating 8-on/40-off dash pattern animated by exactly
              one period (48) loops seamlessly on a path of any length -- no
              getTotalLength measurement, so it can't desync per edge. */}
          <motion.path
            d={spec.d}
            fill="none"
            stroke={color}
            strokeWidth={5}
            strokeLinecap="round"
            strokeDasharray="8 40"
            animate={{ strokeDashoffset: [0, -48] }}
            transition={{ repeat: Infinity, duration: 1.05, ease: "linear" }}
            style={{ filter: `drop-shadow(0 0 5px ${color})` }}
          />
        </>
      )}

      <text
        x={spec.lx} y={spec.ly}
        textAnchor={spec.anchor}
        className="font-mono"
        fontSize={10}
        fill={active ? color : n.labelIdle}
        style={{ transition: "fill 400ms ease" }}
      >
        {spec.label}
      </text>
    </g>
  );
}

function Node({ spec, active, n }: { spec: NodeSpec & { color: string }; active: boolean; n: Neutral }) {
  const { x, y, w, h, icon: Icon, color } = spec;
  return (
    <g>
      {active && (
        <motion.rect
          x={x - 4} y={y - 4} width={w + 8} height={h + 8} rx={8}
          fill="none" stroke={color} strokeWidth={1.5}
          animate={{ opacity: [0.55, 0.1, 0.55] }}
          transition={{ repeat: Infinity, duration: 2.1, ease: "easeInOut" }}
        />
      )}
      <rect
        x={x} y={y} width={w} height={h} rx={6}
        fill={active ? "var(--color-accent-soft)" : n.nodeIdleFill}
        stroke={active ? color : n.nodeIdleStroke}
        strokeWidth={1.5}
        style={{ transition: "fill 400ms ease, stroke 400ms ease" }}
      />
      {/* Icon left, two lines of text right, the whole group centred on the
          box's own mid-line -- so the same layout holds for the tall boxes
          and the short ones without the icon ever colliding with the title. */}
      <Icon
        x={x + 18} y={y + h / 2 - 13} width={26} height={26}
        color={active ? color : n.iconIdle}
        strokeWidth={2}
      />
      <text x={x + 54} y={y + h / 2 - 2} fontSize={15} fontWeight={700} fill={active ? n.titleActive : n.titleIdle}>
        {spec.title}
      </text>
      <text x={x + 54} y={y + h / 2 + 16} fontSize={9.5} className="font-mono" fill={n.sub}>
        {spec.sub}
      </text>
    </g>
  );
}
