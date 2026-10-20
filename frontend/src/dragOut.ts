import { useSyncExternalStore, type DragEvent } from "react";

type DesktopApi = { drag_asset?: (assetId: number) => Promise<string | null> };

declare global {
  interface Window {
    pywebview?: { api?: DesktopApi };
  }
}

export const DRAG_HINT = "Drag into Blender or Maya to import";

export const canDragOut = (): boolean => typeof window.pywebview?.api?.drag_asset === "function";

// The desktop app injects window.pywebview after the page has rendered, then fires this event.
const subscribe = (onChange: () => void) => {
  window.addEventListener("pywebviewready", onChange);
  return () => window.removeEventListener("pywebviewready", onChange);
};

export const useDragOut = (): boolean => useSyncExternalStore(subscribe, canDragOut, () => false);

export function dragOut(e: DragEvent, assetId: number, onError: (message: string) => void): void {
  const drag = window.pywebview?.api?.drag_asset;
  if (!drag) return;
  e.preventDefault();
  drag(assetId).then((error) => error && onError(error), (err: unknown) => onError(String(err)));
}
