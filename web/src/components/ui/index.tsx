/**
 * Design-system primitives.
 *
 * Every surface in the app composes from these so that spacing, radii,
 * borders and state colour are decided once. Two rules they encode:
 *
 *   - colour means state, never decoration. `tone` is a semantic word
 *     ("success", "running"), never a hue, so a component cannot pick a
 *     colour that does not correspond to something real.
 *   - a value that was never measured renders as "Not recorded", not as a
 *     zero or a dash that could be mistaken for a measurement.
 */
import { forwardRef, useEffect, useRef, useState } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Check, AlertTriangle, Info as InfoIcon, Loader2 } from "lucide-react";

/* ── tone ──────────────────────────────────────────────────────────────── */

export type Tone = "neutral" | "accent" | "success" | "warning" | "error" | "info" | "running";

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-ink-2",
  accent: "text-accent",
  success: "text-success",
  warning: "text-warning",
  error: "text-error",
  info: "text-info",
  running: "text-accent",
};

const TONE_BADGE: Record<Tone, string> = {
  neutral: "border-hairline-strong text-ink-2 bg-surface-2",
  accent: "border-accent/35 text-accent bg-accent-soft",
  success: "border-success/35 text-success bg-success-soft",
  warning: "border-warning/35 text-warning bg-warning-soft",
  error: "border-error/35 text-error bg-error-soft",
  info: "border-info/35 text-info bg-info-soft",
  running: "border-accent/35 text-accent bg-accent-soft",
};

/** Maps the backend's own vocabulary onto tone, in one place, so a status
 *  string never gets coloured differently on two screens. */
export function statusTone(status: string | null | undefined): Tone {
  switch ((status ?? "").toLowerCase()) {
    case "ready": case "passed": case "healthy": case "merged": case "won":
    case "verified": case "completed": case "succeeded": case "approved":
      return "success";
    case "running": case "building": case "exploring": case "evaluating":
    case "in_progress": case "pending": case "queued":
      return "running";
    case "degraded": case "simulated": case "warn": case "base_changed":
    case "tie": case "awaiting_review":
      return "warning";
    case "not_ready": case "failed": case "error": case "patch_failed":
    case "lost": case "abandoned": case "rejected":
      return "error";
    case "reverted": case "skipped": case "unavailable":
      return "neutral";
    default:
      return "neutral";
  }
}

/* ── Button ────────────────────────────────────────────────────────────── */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  icon?: ReactNode;
  busy?: boolean;
};

const BUTTON_VARIANT = {
  primary: "bg-accent text-white border-accent hover:bg-accent-hover hover:border-accent-hover",
  secondary: "bg-surface text-ink border-hairline-strong hover:bg-surface-2",
  ghost: "bg-transparent text-ink-2 border-transparent hover:bg-surface-2 hover:text-ink",
  danger: "bg-surface text-error border-error/40 hover:bg-error-soft",
} as const;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, busy, children, className = "", ...rest }, ref,
) {
  const pad = size === "sm" ? "px-2.5 py-1 text-[12px]" : "px-3 py-1.5 text-[13px]";
  return (
    <button
      ref={ref}
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex items-center gap-1.5 rounded-[4px] border font-medium
        transition-colors disabled:opacity-50 disabled:pointer-events-none
        ${BUTTON_VARIANT[variant]} ${pad} ${className}`}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
      {children}
    </button>
  );
});

/* ── Badge / Status ────────────────────────────────────────────────────── */

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-[4px] border px-1.5 py-0.5
      font-mono text-[11px] leading-none ${TONE_BADGE[tone]}`}>
      {children}
    </span>
  );
}

/** A status dot plus its label. The dot only animates for `running`, because
 *  a pulsing indicator is a claim that something is happening right now. */
export function Status({ status, label }: { status: string; label?: string }) {
  const tone = statusTone(status);
  return (
    <span className={`inline-flex items-center gap-1.5 text-[12px] ${TONE_TEXT[tone]}`}>
      <span
        className={`h-[7px] w-[7px] flex-none rounded-full bg-current
          ${tone === "running" ? "running-pulse" : ""}`}
      />
      {label ?? status.replace(/_/g, " ")}
    </span>
  );
}

/* ── Metric ────────────────────────────────────────────────────────────── */

/** A single measured value. `value == null` renders "Not recorded" rather
 *  than 0 or an em dash -- the difference between "we measured zero" and "we
 *  never measured" is load-bearing throughout this product. */
export function Metric({
  label, value, sub, tone = "neutral", mono = true,
}: {
  label: string;
  value: string | number | null | undefined;
  sub?: string;
  tone?: Tone;
  mono?: boolean;
}) {
  const missing = value === null || value === undefined;
  return (
    <div className="min-w-0">
      <div className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-muted">
        {label}
      </div>
      <div
        className={`truncate ${mono ? "font-mono" : ""} tabular text-[20px] font-semibold leading-tight
          ${missing ? "text-muted" : TONE_TEXT[tone]}`}
      >
        {missing ? <span className="text-[13px] font-normal italic">Not recorded</span> : value}
      </div>
      {sub && <div className="mt-0.5 truncate text-[11.5px] text-muted">{sub}</div>}
    </div>
  );
}

/* ── Panel ─────────────────────────────────────────────────────────────── */

export function Panel({
  title, actions, children, className = "", dense = false,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  dense?: boolean;
}) {
  return (
    <section className={`flex min-h-0 flex-col overflow-hidden rounded-[6px] border
      border-hairline bg-surface ${className}`}>
      {title && (
        <header className="flex flex-none items-center justify-between gap-3 border-b
          border-hairline px-4 py-2.5">
          <h2 className="truncate text-[12px] font-semibold uppercase tracking-wide text-ink-2">
            {title}
          </h2>
          {actions && <div className="flex flex-none items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={`min-h-0 flex-1 overflow-auto ${dense ? "" : "p-4"}`}>{children}</div>
    </section>
  );
}

/* ── Table ─────────────────────────────────────────────────────────────── */

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  );
}

export function Th({ children, align = "left" }: { children?: ReactNode; align?: "left" | "right" }) {
  return (
    <th className={`sticky top-0 z-10 whitespace-nowrap border-b border-hairline bg-surface
      px-3 py-2 text-${align} text-[11px] font-semibold uppercase tracking-wide text-muted`}>
      {children}
    </th>
  );
}

export function Td({
  children, align = "left", mono = false, className = "",
}: {
  children?: ReactNode; align?: "left" | "right"; mono?: boolean; className?: string;
}) {
  return (
    <td className={`border-b border-hairline px-3 py-2 text-${align}
      ${mono ? "font-mono tabular text-[12px]" : ""} ${className}`}>
      {children}
    </td>
  );
}

export function Tr({
  children, onClick, selected,
}: { children: ReactNode; onClick?: () => void; selected?: boolean }) {
  return (
    <tr
      onClick={onClick}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => e.key === "Enter" && onClick() : undefined}
      className={`transition-colors last:[&>td]:border-b-0
        ${onClick ? "cursor-pointer hover:bg-surface-2" : ""}
        ${selected ? "bg-accent-soft" : ""}`}
    >
      {children}
    </tr>
  );
}

/* ── Drawer ────────────────────────────────────────────────────────────── */

/** Right-hand inspector. Preferred over navigation so the user never loses
 *  the graph or table they were reading. */
export function Drawer({
  open, onClose, title, subtitle, children, width = 420,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            className="fixed inset-0 z-40 bg-ink/20"
          />
          <motion.aside
            role="dialog"
            aria-label={typeof title === "string" ? title : "Inspector"}
            initial={{ x: width }} animate={{ x: 0 }} exit={{ x: width }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            style={{ width }}
            className="fixed inset-y-0 right-0 z-50 flex flex-col border-l border-hairline
              bg-surface shadow-[var(--shadow-modal)]"
          >
            <header className="flex flex-none items-start justify-between gap-3 border-b
              border-hairline px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-semibold text-ink">{title}</div>
                {subtitle && (
                  <div className="mt-0.5 truncate font-mono text-[11.5px] text-muted">{subtitle}</div>
                )}
              </div>
              <button
                onClick={onClose}
                aria-label="Close"
                className="flex-none rounded-[4px] p-1 text-muted transition-colors
                  hover:bg-surface-2 hover:text-ink"
              >
                <X className="h-4 w-4" />
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-auto p-4">{children}</div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

/* ── Dialog ────────────────────────────────────────────────────────────── */

/** Confirmation for destructive or irreversible actions. `consequences` is
 *  required: a dialog that does not say what will happen is just friction. */
export function Dialog({
  open, onClose, title, consequences, confirmLabel, onConfirm, tone = "error", busy, children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  consequences: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  tone?: "error" | "accent";
  busy?: boolean;
  children?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            onClick={onClose}
            className="absolute inset-0 bg-ink/25"
          />
          <motion.div
            role="alertdialog"
            initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98 }} transition={{ duration: 0.14 }}
            className="relative w-full max-w-md rounded-[8px] border border-hairline bg-surface
              shadow-[var(--shadow-modal)]"
          >
            <div className="px-5 pt-5">
              <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
              <div className="mt-2 text-[13px] leading-relaxed text-ink-2">{consequences}</div>
              {children && <div className="mt-3">{children}</div>}
            </div>
            <div className="mt-5 flex justify-end gap-2 border-t border-hairline px-5 py-3">
              <Button onClick={onClose} disabled={busy}>Cancel</Button>
              <Button
                variant={tone === "error" ? "danger" : "primary"}
                onClick={onConfirm}
                busy={busy}
              >
                {confirmLabel}
              </Button>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

/* ── Tabs ──────────────────────────────────────────────────────────────── */

export function Tabs<T extends string>({
  tabs, active, onChange,
}: { tabs: readonly { id: T; label: string }[]; active: T; onChange: (id: T) => void }) {
  return (
    <div role="tablist" className="flex gap-0.5 border-b border-hairline">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors
            ${active === t.id
              ? "border-accent text-ink"
              : "border-transparent text-muted hover:text-ink-2"}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ── Field / KeyValue ──────────────────────────────────────────────────── */

/** Definition-list row. `value == null` shows "Not recorded". */
export function Field({
  k, v, mono = true, tone,
}: { k: string; v: ReactNode; mono?: boolean; tone?: Tone }) {
  const missing = v === null || v === undefined || v === "";
  return (
    <>
      <dt className="py-1 pr-3 text-[12px] text-muted">{k}</dt>
      <dd className={`min-w-0 break-words py-1 text-[12.5px]
        ${mono ? "font-mono" : ""} ${missing ? "italic text-muted" : tone ? TONE_TEXT[tone] : "text-ink-2"}`}>
        {missing ? "Not recorded" : v}
      </dd>
    </>
  );
}

export function FieldList({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[minmax(90px,auto)_1fr] gap-x-2">{children}</dl>;
}

/* ── EmptyState ────────────────────────────────────────────────────────── */

export function EmptyState({
  title, body, action,
}: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <h3 className="text-[14px] font-semibold text-ink">{title}</h3>
      <p className="mt-1 max-w-sm text-[13px] leading-relaxed text-ink-2">{body}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* ── ErrorState ────────────────────────────────────────────────────────── */

/** Errors name the failing component and what to do next. "Something went
 *  wrong" is never acceptable in an engineering tool. */
export function ErrorState({
  title, detail, remediation, onRetry,
}: { title: string; detail: string; remediation?: string; onRetry?: () => void }) {
  return (
    <div className="rounded-[6px] border border-error/30 bg-error-soft px-4 py-3">
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 flex-none text-error" />
        <span className="text-[13px] font-semibold text-error">{title}</span>
      </div>
      <p className="mt-1.5 font-mono text-[11.5px] leading-relaxed text-ink-2">{detail}</p>
      {remediation && <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">{remediation}</p>}
      {onRetry && <Button size="sm" className="mt-2.5" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

/* ── Skeleton ──────────────────────────────────────────────────────────── */

/** Shaped like the content it replaces, so layout does not jump on load. */
export function Skeleton({ rows = 3, className = "" }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`} aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-[13px] animate-pulse rounded-[3px] bg-surface-3"
          style={{ width: `${100 - (i % 3) * 14}%` }}
        />
      ))}
    </div>
  );
}

/* ── Tooltip ───────────────────────────────────────────────────────────── */

/** For domain vocabulary only (HLC, AOST, blast radius) -- never to explain
 *  a button that already has a label. */
export function Tooltip({ tip, children }: { tip: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => { timer.current = window.setTimeout(() => setOpen(true), 350); }}
      onMouseLeave={() => { window.clearTimeout(timer.current); setOpen(false); }}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span className="cursor-help border-b border-dotted border-muted">{children}</span>
      <AnimatePresence>
        {open && (
          <motion.span
            role="tooltip"
            initial={{ opacity: 0, y: 2 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            className="absolute bottom-full left-0 z-50 mb-1.5 w-64 rounded-[6px] border
              border-hairline bg-surface px-2.5 py-2 text-[12px] leading-relaxed text-ink-2
              shadow-[var(--shadow-popover)]"
          >
            {tip}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  );
}

/* ── Freshness ─────────────────────────────────────────────────────────── */

/** "Updated 4s ago · CockroachDB". Naming the source is what makes a number
 *  auditable rather than merely displayed. */
export function Freshness({ at, source }: { at: number | null; source: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (at === null) return null;
  const secs = Math.max(0, Math.round((Date.now() - at) / 1000));
  const ago = secs < 60 ? `${secs}s` : secs < 3600 ? `${Math.round(secs / 60)}m` : `${Math.round(secs / 3600)}h`;
  return (
    <Tooltip tip={<>Source: {source}. Fetched {ago} ago; this UI never caches a value past its source.</>}>
      <span className="font-mono text-[11px] text-muted">updated {ago} ago · {source}</span>
    </Tooltip>
  );
}

/* ── Toast ─────────────────────────────────────────────────────────────── */

export interface ToastMessage { id: number; text: string; tone: Tone }

export function ToastStack({ toasts, onDismiss }: { toasts: ToastMessage[]; onDismiss: (id: number) => void }) {
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex flex-col gap-2">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }}
            transition={{ duration: 0.16 }}
            onClick={() => onDismiss(t.id)}
            className="pointer-events-auto flex cursor-pointer items-center gap-2 rounded-[6px]
              border border-hairline bg-surface px-3 py-2 text-[12.5px] text-ink
              shadow-[var(--shadow-popover)]"
          >
            {t.tone === "success" ? <Check className="h-3.5 w-3.5 text-success" />
              : t.tone === "error" ? <AlertTriangle className="h-3.5 w-3.5 text-error" />
              : <InfoIcon className="h-3.5 w-3.5 text-info" />}
            {t.text}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
