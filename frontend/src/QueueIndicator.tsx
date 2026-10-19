import { useCallback, useEffect, useState } from "react";
import { api, type QueueState } from "./api";
import { useEvents } from "./useEvents";

export function queueText(s: QueueState): string | null {
  if (s.paused) return `Paused · ${s.queued} queued`;
  if (!s.running && !s.queued) return null;
  return `Converting ${s.running}${s.queued ? ` · ${s.queued} queued` : ""}`;
}

export function QueueIndicator() {
  const [state, setState] = useState<QueueState>({ queued: 0, running: 0, paused: false });
  const refetch = useCallback(() => {
    api.getQueue().then(setState, () => undefined);
  }, []);
  useEffect(refetch, [refetch]);
  useEvents((ev) => {
    if (ev.type === "queue.updated") setState(ev.data);
  }, refetch);

  const text = queueText(state);
  const toggle = () => (state.paused ? api.resumeQueue() : api.pauseQueue()).then(setState, refetch);

  return (
    <span className="flex items-center gap-2 text-neutral-300">
      <span role="status" aria-live="polite">{text}</span>
      {text && (
        <button type="button" className="rounded bg-neutral-800 px-2 py-1 hover:bg-neutral-700" onClick={toggle}>
          {state.paused ? "Resume" : "Pause"}
        </button>
      )}
    </span>
  );
}
