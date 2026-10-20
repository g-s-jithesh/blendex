import { useCallback, useEffect, useRef, useState } from "react";
import type { Asset } from "./api";

export const HOVER_DELAY_MS = 2000;
export const HOVER_BAR_DELAY_MS = 300;
export const VIEWPORT_ATTR = "data-hover-viewport";

export type ActivePreview = { asset: Asset; el: HTMLElement; signal: AbortSignal };

export function useHoverPreview(delayMs: number = HOVER_DELAY_MS) {
  const [active, setActive] = useState<ActivePreview | null>(null);
  const [armed, setArmed] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const controller = useRef<AbortController | null>(null);
  const target = useRef<{ asset: Asset; el: HTMLElement } | null>(null);

  const cancel = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = undefined;
    controller.current?.abort();
    controller.current = null;
    target.current = null;
    setActive(null);
    setArmed(null);
  }, []);

  const enter = useCallback(
    (asset: Asset, el: HTMLElement) => {
      if (target.current?.asset.id === asset.id) return;
      cancel();
      if (!asset.has_proxy) return;
      target.current = { asset, el };
      setArmed(asset.id);
      timer.current = setTimeout(() => {
        timer.current = undefined;
        const ctrl = new AbortController();
        controller.current = ctrl;
        setArmed(null);
        setActive({ asset, el, signal: ctrl.signal });
      }, delayMs);
    },
    [cancel, delayMs],
  );

  // Moving between the card and the overlay drawn on top of it is not a leave.
  const leave = useCallback(
    (related?: EventTarget | null) => {
      if (related instanceof Node) {
        if (target.current?.el.contains(related)) return;
        if (related instanceof Element && related.closest(`[${VIEWPORT_ATTR}]`)) return;
      }
      cancel();
    },
    [cancel],
  );

  useEffect(() => {
    const onScroll = () => cancel();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      cancel();
    };
  }, [cancel]);

  return { active, armed, enter, leave };
}
