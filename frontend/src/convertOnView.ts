import { useEffect, type RefObject } from "react";
import { api, type Asset } from "./api";

export const VIEW_DWELL_MS = 400;
export const VIEW_RATIO = 0.5;

const requested = new Set<string>();

function request(id: number, mtime: number): void {
  const key = `${id}:${mtime}`;
  if (requested.has(key)) return;
  requested.add(key);
  api.convertAsset(id).catch(() => requested.delete(key));
}

export function requestConversion(a: Asset): void {
  if (a.status === "pending") request(a.id, a.mtime);
}

export function useConvertOnView(ref: RefObject<HTMLElement | null>, asset: Asset): void {
  const pending = asset.status === "pending";
  const { id, mtime } = asset;
  useEffect(() => {
    const el = ref.current;
    if (!pending || !el) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const io = new IntersectionObserver(([e]) => {
      clearTimeout(timer);
      if (e.intersectionRatio >= VIEW_RATIO) timer = setTimeout(() => request(id, mtime), VIEW_DWELL_MS);
    }, { threshold: VIEW_RATIO });
    io.observe(el);
    return () => {
      io.disconnect();
      clearTimeout(timer);
    };
  }, [ref, pending, id, mtime]);
}
