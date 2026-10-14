import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { GitBranch, Sparkles } from "lucide-react";

interface InitialLoaderProps {
  /** True once the first real backend answer (readiness probe) has arrived,
   *  whether it succeeded or failed. The loader stays up until then. */
  ready: boolean;
  /** What the app is actually waiting on, shown verbatim. */
  status: string;
  /** Hard cap so a hung backend never blocks the UI; the shell shows OFFLINE. */
  maxWaitMs?: number;
}

export function InitialLoader({ ready, status, maxWaitMs = 8000 }: InitialLoaderProps) {
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setTimedOut(true), maxWaitMs);
    return () => clearTimeout(t);
  }, [maxWaitMs]);

  const visible = !ready && !timedOut;
  const statusText = status;

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="initial-app-loader"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0, scale: 0.99, filter: "blur(6px)" }}
          transition={{ duration: 0.35, ease: "easeInOut" }}
          className="fixed inset-0 z-[9999] flex flex-col items-center justify-center bg-slate-50 dark:bg-[#0c0e14] select-none"
        >
          {/* Subtle background radial glow */}
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-indigo-500/10 via-transparent to-transparent pointer-events-none" />

          <div className="relative z-10 flex flex-col items-center text-center px-4 max-w-sm">
            {/* Animated Brand Icon with concentric glowing rings */}
            <div className="relative mb-6 flex h-20 w-20 items-center justify-center">
              {/* Outer soft glowing ping */}
              <div className="absolute inset-0 rounded-2xl bg-indigo-500/20 blur-xl animate-pulse" />

              {/* Spinning gradient border ring */}
              <div
                className="absolute inset-0 rounded-2xl p-[2px] animate-spin"
                style={{
                  background: "conic-gradient(from 0deg, #6366f1, #3b82f6, #10b981, transparent 75%)",
                  animationDuration: "2.5s",
                }}
              >
                <div className="h-full w-full rounded-[14px] bg-slate-50 dark:bg-[#0c0e14]" />
              </div>

              {/* Center icon container */}
              <div className="relative flex h-14 w-14 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-600 via-indigo-500 to-blue-600 text-white shadow-lg shadow-indigo-500/30">
                {/* 3D Isometric Mini-cube SVG */}
                <svg viewBox="0 0 40 40" className="h-8 w-8 drop-shadow" fill="none">
                  {/* Top face */}
                  <polygon points="20,8 32,15 20,22 8,15" fill="#a5b4fc" />
                  {/* Left face */}
                  <polygon points="8,15 20,22 20,34 8,27" fill="#6366f1" />
                  {/* Right face */}
                  <polygon points="20,22 32,15 32,27 20,34" fill="#4338ca" />
                </svg>
              </div>

              {/* Orbiting glowing node */}
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
                className="absolute inset-[-6px]"
              >
                <div className="h-2.5 w-2.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
              </motion.div>
            </div>

            {/* Title & Tagline */}
            <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-white flex items-center gap-1.5">
              <span>Branchable Memory</span>
              <Sparkles className="h-4 w-4 text-indigo-500 animate-pulse" />
            </h1>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400 font-medium">
              Agent Belief OS
            </p>

            {/* Animated Loading Bar */}
            <div className="mt-5 w-48 h-1 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden relative">
              <div
                className="absolute inset-y-0 w-24 rounded-full bg-gradient-to-r from-indigo-500 via-blue-500 to-emerald-400 animate-[shimmer_1.4s_infinite]"
              />
            </div>

            {/* Dynamic Status Text */}
            <div className="mt-3 flex items-center gap-2 text-[11.5px] font-mono text-slate-500 dark:text-slate-400">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-500" />
              </span>
              <span className="truncate">{statusText}</span>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** Lightweight matching view loader for Suspense tab fallbacks */
export function ViewLoader({ label = "Loading view..." }: { label?: string }) {
  return (
    <div className="flex h-96 w-full flex-col items-center justify-center gap-3">
      <div className="relative flex h-12 w-12 items-center justify-center">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400/20" />
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent dark:border-indigo-400 dark:border-t-transparent" />
        <GitBranch className="absolute h-5 w-5 text-indigo-600 dark:text-indigo-400" />
      </div>
      <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 animate-pulse">
        {label}
      </span>
    </div>
  );
}
