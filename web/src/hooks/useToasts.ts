import { useCallback, useRef, useState } from "react";
import type { ToastMessage, Tone } from "../components/ui";

const DISMISS_MS = 4200;

/**
 * Transient confirmations only.
 *
 * A toast may say a proof finished; it may never carry the proof's result.
 * Anything a reader might need to check, quote or screenshot stays on the page
 * -- a notification that disappears after four seconds is the wrong container
 * for evidence, and this project's whole argument is that evidence has to
 * stay put.
 */
export function useToasts() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const next = useRef(1);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (text: string, tone: Tone = "neutral") => {
      const id = next.current++;
      setToasts((list) => [...list.slice(-3), { id, text, tone }]);
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), DISMISS_MS),
      );
      return id;
    },
    [dismiss],
  );

  return { toasts, push, dismiss };
}
