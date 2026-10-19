import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Asset, AssetEvent, AssetPage, EventType } from "./api";
import { applyEvent, mergePage, PAGE_SIZE, useAssets, type AssetFilters } from "./useAssets";

const asset = (id: number, name: string, ext = "blend"): Asset => ({
  id, library_id: 1, path: `/l/${name}.${ext}`, name, ext, size: 1, mtime: 1, dcc_version: null,
  status: "pending", poly_count: null, error: null, has_thumbnail: false, has_proxy: false, favorite: false, tags: [],
});
const ev = (type: EventType, data: Asset): AssetEvent => ({ type, asset_id: data.id, data });
const ids = (p: AssetPage) => p.items.map((a) => a.id);

afterEach(() => vi.unstubAllGlobals());

test("applyEvent keeps name,id order, filters and total", () => {
  let page = { items: [asset(1, "b")], total: 1 };
  page = applyEvent(page, ev("asset.added", asset(2, "a")), {});
  expect(page.items.map((a) => a.id)).toEqual([2, 1]);
  expect(page.total).toBe(2);

  page = applyEvent(page, ev("asset.added", asset(3, "c", "fbx")), { ext: "blend" });
  expect(page.total).toBe(2);

  page = applyEvent(page, ev("conversion.done", { ...asset(1, "b"), status: "ready" }), {});
  expect(page.items[1].status).toBe("ready");
  expect(page.total).toBe(2);

  page = applyEvent(page, ev("asset.updated", asset(2, "a")), { q: "B" });
  expect(page.items.map((a) => a.id)).toEqual([1]);
  expect(page.total).toBe(1);

  page = applyEvent(page, ev("asset.removed", asset(1, "b")), {});
  expect(page).toEqual({ items: [], total: 0 });
});

test("on a partly loaded list, events inside the loaded prefix patch items and later ones only move total", () => {
  let page: AssetPage = { items: [asset(1, "a"), asset(2, "b"), asset(3, "d")], total: 10 };
  page = applyEvent(page, ev("asset.added", asset(4, "c")), {});
  expect([ids(page), page.total]).toEqual([[1, 2, 4, 3], 11]);

  page = applyEvent(page, ev("asset.added", asset(5, "z")), {});
  expect([ids(page), page.total]).toEqual([[1, 2, 4, 3], 12]);

  page = applyEvent(page, ev("conversion.done", { ...asset(6, "y"), status: "ready" }), {});
  page = applyEvent(page, ev("asset.removed", asset(7, "x")), {});
  expect([ids(page), page.total]).toEqual([[1, 2, 4, 3], 11]);

  page = applyEvent(page, ev("asset.removed", asset(2, "b")), {});
  expect([ids(page), page.total]).toEqual([[1, 4, 3], 10]);

  const fav = { ...asset(8, "bb"), favorite: true };
  let favs: AssetPage = { items: [{ ...asset(1, "a"), favorite: true }, { ...asset(3, "d"), favorite: true }], total: 5 };
  favs = applyEvent(favs, ev("asset.updated", fav), { favorite: true });
  expect([ids(favs), favs.total]).toEqual([[1, 8, 3], 6]);
});

test("mergePage appends the next page in order, drops duplicates and ends at an empty page", () => {
  let page: AssetPage = { items: [asset(1, "a"), asset(2, "b")], total: 4 };
  page = applyEvent(page, ev("asset.added", asset(3, "ab")), {});
  page = mergePage(page, { items: [asset(2, "b"), asset(4, "c"), asset(5, "d")], total: 5 });
  expect([ids(page), page.total]).toEqual([[1, 3, 2, 4, 5], 5]);

  expect(mergePage({ items: [asset(1, "a")], total: 3 }, { items: [], total: 3 }).total).toBe(1);
});

test("useAssets pages in on sentinel visibility, resets on filter change and aborts the stale request", async () => {
  const all = Array.from({ length: 450 }, (_, i) => asset(i + 1, `m${String(i).padStart(3, "0")}`));
  const calls: { params: URLSearchParams; signal: AbortSignal }[] = [];
  vi.stubGlobal("fetch", (u: string, init: RequestInit) => {
    const params = new URL(u, "http://x").searchParams;
    calls.push({ params, signal: init.signal as AbortSignal });
    const hits = all.filter((a) => a.name.includes(params.get("q") ?? ""));
    const offset = Number(params.get("offset"));
    const body = { items: hits.slice(offset, offset + Number(params.get("limit"))), total: hits.length };
    return Promise.resolve(new Response(JSON.stringify(body)));
  });
  vi.stubGlobal("WebSocket", class { close() {} });
  let visible = false;
  let see: () => void = () => {};
  vi.stubGlobal("IntersectionObserver", class {
    constructor(cb: (e: { isIntersecting: boolean }[]) => void) {
      see = () => cb([{ isIntersecting: visible }]);
    }
    observe() {
      see();
    }
    disconnect() {}
  });
  const sentinel = { current: document.createElement("div") };
  const { result, rerender } = renderHook((f: AssetFilters) => useAssets(f, sentinel), { initialProps: {} });

  await waitFor(() => expect(result.current.page.items).toHaveLength(PAGE_SIZE));
  expect(result.current.page.total).toBe(450);
  expect(calls).toHaveLength(1);
  visible = true;
  act(() => see());
  await waitFor(() => expect(result.current.page.items).toHaveLength(450));
  expect(ids(result.current.page)).toEqual(all.map((a) => a.id));
  expect(calls.map((c) => c.params.get("offset"))).toEqual(["0", "200", "400"]);

  rerender({ q: "m0" });
  rerender({ q: "m1" });
  await waitFor(() => expect(result.current.page.total).toBe(100));
  expect(calls.at(-2)?.signal.aborted).toBe(true);
  expect(result.current.page.items.every((a) => a.name.startsWith("m1"))).toBe(true);
  expect(result.current.page.items).toHaveLength(100);
});
