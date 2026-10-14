import { useCallback, useEffect, useState } from "react";
import { fetchMerges } from "../lib/api";
import type { MergeRow } from "../lib/types";

/** Merges aren't pushed over SSE (they're a small, low-frequency append-only
 * log, not worth a dedicated stream) -- refetched whenever the tree changes,
 * same trigger the previous frontend used, plus exposed as a manual
 * `refresh` for right after a revert call resolves. */
export function useMerges(rootId: string | null, refreshKey: unknown) {
  const [merges, setMerges] = useState<MergeRow[]>([]);

  const refresh = useCallback(() => {
    if (!rootId) { setMerges([]); return; }
    fetchMerges(rootId).then(setMerges).catch(() => { /* next tree event retries */ });
  }, [rootId]);

  useEffect(() => {
    refresh();
    // refreshKey (the latest tree snapshot) is the actual dependency here --
    // refresh() itself only changes identity when rootId does.
  }, [refresh, refreshKey]);

  return { merges, refresh };
}
