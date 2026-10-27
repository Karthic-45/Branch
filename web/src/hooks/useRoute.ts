import { useCallback, useEffect, useState } from "react";
import type { Section } from "../components/layout/AppShell";

const SECTIONS: Section[] = [
  "overview", "decisions", "branches", "memory", "deployments",
  "observability", "aws", "outputs", "proofs", "reports", "audit", "judge",
];

export interface Route {
  section: Section;
  /** Decision (merge) id when the URL names one: #/decisions/<uuid>. */
  decisionId: string | null;
}

const DEFAULT: Route = { section: "overview", decisionId: null };

function parse(hash: string): Route {
  const path = hash.replace(/^#\/?/, "").split("?")[0];
  if (!path) return DEFAULT;
  const [head, tail] = path.split("/");
  const section = SECTIONS.find((s) => s === head);
  if (!section) return DEFAULT;
  return { section, decisionId: section === "decisions" && tail ? tail : null };
}

function serialize(route: Route): string {
  const tail = route.section === "decisions" && route.decisionId ? `/${route.decisionId}` : "";
  return `#/${route.section}${tail}`;
}

/**
 * Hash routing, hand-rolled.
 *
 * The hash rather than the History API because the production build is served
 * from a StaticFiles mount with no rewrite rule -- a real path like
 * /decisions/<id> would 404 on reload, and adding a catch-all to FastAPI is
 * the sort of change that ends up shadowing /api. The hash never reaches the
 * server.
 *
 * The query string is deliberately untouched: `?root=<uuid>` selects which
 * project is loaded and has to survive every navigation, so it lives in the
 * search params and the section lives in the hash. They do not interact.
 */
export function useRoute(): [Route, (next: Partial<Route>) => void] {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));

  // Both events, deliberately. pushState does not fire hashchange, so forward
  // navigation is handled locally in navigate(); going Back fires popstate and
  // hashchange together, and a hand-edited address bar fires only hashchange.
  // Listening to one of the two leaves a real case unhandled.
  useEffect(() => {
    const sync = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);

  // Stamp a bare URL with the default route so the first Back press has
  // somewhere to go, and so a copied URL always names its own view.
  useEffect(() => {
    if (!window.location.hash) {
      // Hash only. A relative URL starting with "#" keeps the path and the
      // query string; concatenating location.search here instead duplicated
      // ?root= into the fragment, producing /?root=x#/overview?root=x.
      window.history.replaceState(null, "", serialize(DEFAULT));
    }
  }, []);

  const navigate = useCallback((next: Partial<Route>) => {
    setRoute((current) => {
      // Changing section clears a decision selection unless the caller sets
      // one in the same call -- otherwise #/branches would keep a stale
      // decision id alive and restore it on the next visit to #/decisions.
      const merged: Route = {
        section: next.section ?? current.section,
        decisionId:
          next.decisionId !== undefined
            ? next.decisionId
            : next.section && next.section !== current.section
              ? null
              : current.decisionId,
      };
      const target = serialize(merged);
      if (window.location.hash !== target) {
        window.history.pushState(null, "", target);
      }
      return merged;
    });
  }, []);

  return [route, navigate];
}
