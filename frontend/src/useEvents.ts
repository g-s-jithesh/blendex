import { useEffect, useRef } from "react";
import type { ServerEvent } from "./api";

export const EVENTS_PATH = "/ws/events";

export function useEvents(onEvent: (e: ServerEvent) => void, onReconnect: () => void): void {
  const handlers = useRef({ onEvent, onReconnect });
  handlers.current = { onEvent, onReconnect };

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    let stopped = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}${EVENTS_PATH}`);
      ws.onopen = () => {
        if (attempts > 0) handlers.current.onReconnect();
        attempts = 0;
      };
      ws.onmessage = (m) => {
        try {
          handlers.current.onEvent(JSON.parse(String(m.data)) as ServerEvent);
        } catch {
          return;
        }
      };
      ws.onclose = () => {
        if (stopped) return;
        attempts += 1;
        timer = setTimeout(connect, Math.min(1000 * attempts, 10000));
      };
    };
    connect();

    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);
}
