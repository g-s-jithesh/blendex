import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { api, errorText, type Asset, type AssetEvent, type AssetPage } from "./api";
import { useEvents } from "./useEvents";

export type AssetFilters = { library_id?: number; q?: string; ext?: string; favorite?: boolean; tag?: string };

export const PAGE_SIZE = 200;
export const SENTINEL_MARGIN = "1200px";

export function matchesFilters(a: Asset, f: AssetFilters): boolean {
  if (f.library_id !== undefined && a.library_id !== f.library_id) return false;
  if (f.ext && a.ext !== f.ext) return false;
  if (f.favorite !== undefined && a.favorite !== f.favorite) return false;
  if (f.tag && !a.tags.includes(f.tag)) return false;
  return !f.q || a.name.toLowerCase().includes(f.q.toLowerCase());
}

export const normalizeTag = (raw: string) => raw.trim().toLowerCase();

export function optimisticEdit(
  before: Asset, after: Asset, send: () => Promise<Asset>, apply: (a: Asset) => void,
): Promise<Asset> {
  apply(after);
  return send().then(
    (saved) => {
      apply(saved);
      return saved;
    },
    (e: unknown) => {
      apply(before);
      throw e;
    },
  );
}

const byNameId = (a: Asset, b: Asset) => (a.name < b.name ? -1 : a.name > b.name ? 1 : a.id - b.id);

// items stay a prefix of the server's name,id order, so the next page starts at offset items.length;
// an event past the loaded prefix only moves total and its asset arrives with a later page.
export function applyEvent(page: AssetPage, ev: AssetEvent, f: AssetFilters): AssetPage {
  const had = page.items.some((a) => a.id === ev.asset_id);
  const matches = matchesFilters(ev.data, f);
  const keep = ev.type !== "asset.removed" && matches;
  const last = page.items.at(-1);
  const inPrefix = had || page.items.length >= page.total || (last !== undefined && byNameId(ev.data, last) < 0);
  if (!inPrefix) {
    const delta = ev.type === "asset.added" ? Number(matches) : ev.type === "asset.removed" ? -Number(matches) : 0;
    return { items: page.items, total: page.total + delta };
  }
  const items = page.items.filter((a) => a.id !== ev.asset_id);
  if (keep) items.push(ev.data);
  items.sort(byNameId);
  return { items, total: page.total + Number(keep) - Number(had) };
}

export function mergePage(page: AssetPage, next: AssetPage): AssetPage {
  const seen = new Set(page.items.map((a) => a.id));
  const items = [...page.items, ...next.items.filter((a) => !seen.has(a.id))].sort(byNameId);
  return { items, total: next.items.length ? next.total : items.length };
}

export function useAssets(filters: AssetFilters, sentinel: RefObject<HTMLElement | null>) {
  const [page, setPage] = useState<AssetPage>({ items: [], total: 0 });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const current = useRef(filters);
  current.current = filters;
  const inflight = useRef<AbortController | null>(null);

  const load = useCallback((offset: number) => {
    inflight.current?.abort();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    setLoading(offset === 0);
    setFetching(true);
    api
      .listAssets({ ...current.current, limit: PAGE_SIZE, offset }, ctrl.signal)
      .then((p) => {
        if (ctrl.signal.aborted) return;
        setPage((prev) => (offset === 0 ? p : mergePage(prev, p)));
        setError(null);
      })
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (inflight.current !== ctrl) return;
        inflight.current = null;
        setLoading(false);
        setFetching(false);
      });
  }, []);
  const refetch = useCallback(() => load(0), [load]);

  const { library_id, q, ext, favorite, tag } = filters;
  useEffect(() => {
    refetch();
    return () => inflight.current?.abort();
  }, [library_id, q, ext, favorite, tag, refetch]);

  const loaded = page.items.length;
  const more = loaded < page.total;
  // A fresh observer per loaded length reports the sentinel's position after the new cards are laid out.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !more || fetching || error) return;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) load(loaded);
    }, { rootMargin: SENTINEL_MARGIN });
    io.observe(el);
    return () => io.disconnect();
  }, [sentinel, more, fetching, error, loaded, load]);

  useEvents((ev) => {
    if (ev.type !== "queue.updated") setPage((p) => applyEvent(p, ev, current.current));
  }, refetch);

  const update = useCallback(
    (a: Asset) => setPage((p) => applyEvent(p, { type: "asset.updated", asset_id: a.id, data: a }, current.current)),
    [],
  );

  return { page, error, loading, refetch, update };
}
