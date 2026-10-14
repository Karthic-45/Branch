import { useEffect, useState } from "react";
import type { BranchNode, ConnState, FeedEvent } from "../lib/types";

export interface EventStreamState {
  nodes: BranchNode[];
  events: FeedEvent[];
  /** Latest stage seen for a branch, derived from the feed. Status alone
   * (on BranchNode) only ever says exploring/merged/abandoned -- this is
   * what lets the UI show *which* of the several steps inside "exploring"
   * a branch is actually on right now. */
  branchStage: Map<string, Stage>;
  connState: ConnState;
}

type Stage = FeedEvent["stage"];

const EMPTY: EventStreamState = {
  nodes: [],
  events: [],
  branchStage: new Map(),
  connState: "idle",
};

/** One SSE connection per root branch, mirroring exactly what
 * src/bmem/api.py's /api/branches/{root_id}/stream sends: a "tree" event
 * with the full node snapshot whenever it changes, and a "log" event with
 * whatever process events are new since the last push. The stream's own
 * first "log" push already carries the full backlog (its sent-count starts
 * at 0 per connection) -- fetching /api/events separately first would
 * double every event, which is exactly the bug the previous frontend had
 * and fixed by deleting that redundant fetch rather than adding one here. */
export function useEventStream(rootId: string | null): EventStreamState {
  const [state, setState] = useState<EventStreamState>(EMPTY);

  useEffect(() => {
    if (!rootId) {
      setState(EMPTY);
      return;
    }

    setState({ ...EMPTY, connState: "connecting" });
    const source = new EventSource(`/api/branches/${encodeURIComponent(rootId)}/stream`);

    source.addEventListener("tree", (event) => {
      const nodes = JSON.parse((event as MessageEvent).data) as BranchNode[];
      setState((prev) => ({ ...prev, nodes, connState: "live" }));
    });

    source.addEventListener("log", (event) => {
      const incoming = JSON.parse((event as MessageEvent).data) as FeedEvent[];
      if (!incoming.length) return;
      setState((prev) => {
        const branchStage = new Map(prev.branchStage);
        for (const ev of incoming) {
          if (ev.branch_id) branchStage.set(ev.branch_id, ev.stage);
        }
        return { ...prev, events: [...prev.events, ...incoming], branchStage };
      });
    });

    source.onerror = () => setState((prev) => ({ ...prev, connState: "disconnected" }));

    return () => source.close();
  }, [rootId]);

  return state;
}
