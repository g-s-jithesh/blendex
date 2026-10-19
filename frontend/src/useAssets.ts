import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, type Asset, type AssetEvent, type AssetPage } from "./api";
import { useEvents } from "./useEvents";

export type AssetFilters = { library_id?: number; q?: string; ext?: string; favorite?: boolean; tag?: string };

// ponytail: single page of 500 (API max); add offset paging when libraries outgrow it.
export const PAGE_LIMIT = 500;

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

export function applyEvent(page: AssetPage, ev: AssetEvent, f: AssetFilters): AssetPage {
  const had = page.items.some((a) => a.id === ev.asset_id);
  const keep = ev.type !== "asset.removed" && matchesFilters(ev.data, f);
  const items = page.items.filter((a) => a.id !== ev.asset_id);
  if (keep) items.push(ev.data);
  items.sort(byNameId);
  const truncated = page.items.length < page.total;
  const delta = !had && truncated && ev.type !== "asset.added" ? 0 : Number(keep) - Number(had);
  return { items: items.slice(0, PAGE_LIMIT), total: page.total + delta };
}

export function useAssets(filters: AssetFilters) {
  const [page, setPage] = useState<AssetPage>({ items: [], total: 0 });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const current = useRef(filters);
  current.current = filters;
  const inflight = useRef<AbortController | null>(null);

  const refetch = useCallback(() => {
    inflight.current?.abort();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    setLoading(true);
    api
      .listAssets({ ...current.current, limit: PAGE_LIMIT }, ctrl.signal)
      .then((p) => {
        setPage(p);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (inflight.current === ctrl) setLoading(false);
      });
  }, []);

  const { library_id, q, ext, favorite, tag } = filters;
  useEffect(() => {
    refetch();
    return () => inflight.current?.abort();
  }, [library_id, q, ext, favorite, tag, refetch]);

  useEvents((ev) => setPage((p) => applyEvent(p, ev, current.current)), refetch);

  const update = useCallback(
    (a: Asset) => setPage((p) => applyEvent(p, { type: "asset.updated", asset_id: a.id, data: a }, current.current)),
    [],
  );

  return { page, error, loading, refetch, update };
}
