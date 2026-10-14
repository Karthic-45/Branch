import { useCallback, useEffect, useState } from "react";
import { fetchReadiness } from "../lib/api";
import type { ReadinessReport } from "../lib/types";

/** Live dependency health.
 *
 * Polled rather than pushed: readiness probes make real calls to Bedrock, S3
 * and CodeBuild, so this deliberately runs on a slow interval and only while
 * the tab is visible. A status this UI shows must correspond to a probe that
 * actually ran, and re-probing every second would cost real quota to tell us
 * something that changes on the order of minutes. */
const INTERVAL_MS = 60_000;

export function useReadiness() {
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  // True once the first probe has returned or failed -- the boot screen waits on it.
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setReadiness(await fetchReadiness());
      setCheckedAt(Date.now());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // Leave the previous report in place: a failed poll is not evidence
      // that the dependencies changed, and blanking the panel would imply it.
    } finally {
      setLoading(false);
      setAttempted(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  return { readiness, checkedAt, loading, attempted, error, refresh };
}
