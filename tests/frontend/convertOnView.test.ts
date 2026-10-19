import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Asset, AssetStatus } from "../../frontend/src/api";
import { requestConversion, useConvertOnView, VIEW_DWELL_MS } from "../../frontend/src/convertOnView";

type Entry = { intersectionRatio: number };
let observers: { cb: (e: Entry[]) => void; disconnected: boolean }[] = [];
const posts: string[] = [];

class FakeObserver {
  rec: { cb: (e: Entry[]) => void; disconnected: boolean };
  constructor(cb: (e: Entry[]) => void) {
    this.rec = { cb, disconnected: false };
    observers.push(this.rec);
  }
  observe() {}
  disconnect() {
    this.rec.disconnected = true;
  }
}

const asset = (id: number, status: AssetStatus = "pending", mtime = 1): Asset => ({
  id, library_id: 1, path: `/l/a${id}.obj`, name: `a${id}`, ext: "obj", size: 1, mtime, dcc_version: null,
  status, poly_count: null, error: null, has_thumbnail: false, has_proxy: false, favorite: false, tags: [],
});

function mount(a: Asset) {
  const ref = { current: document.createElement("div") };
  return renderHook((x: Asset) => useConvertOnView(ref, x), { initialProps: a });
}

const see = (ratio: number) => act(() => observers.at(-1)?.cb([{ intersectionRatio: ratio }]));
const tick = (ms: number) => act(() => vi.advanceTimersByTime(ms));

beforeEach(() => {
  observers = [];
  posts.length = 0;
  vi.useFakeTimers();
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    posts.push(`${init?.method} ${url}`);
    return Promise.resolve(new Response(null, { status: 202 }));
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("a pending card visible for the dwell time requests one conversion", () => {
  mount(asset(1));
  see(0.6);
  tick(VIEW_DWELL_MS - 1);
  expect(posts).toEqual([]);
  tick(1);
  expect(posts).toEqual(["POST /api/assets/1/convert"]);
  see(0);
  see(1);
  tick(VIEW_DWELL_MS);
  expect(posts).toHaveLength(1);
});

test("scrolling past, or less than half visible, requests nothing", () => {
  const h = mount(asset(2));
  see(1);
  tick(VIEW_DWELL_MS - 50);
  see(0.3);
  tick(10_000);
  h.unmount();
  expect(posts).toEqual([]);
  expect(observers[0].disconnected).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

test("only pending cards are observed; a new file version can be requested again", () => {
  const h = mount(asset(3, "ready"));
  expect(observers).toHaveLength(0);
  h.rerender(asset(3, "pending", 2));
  see(1);
  tick(VIEW_DWELL_MS);
  h.rerender(asset(3, "pending", 3));
  see(1);
  tick(VIEW_DWELL_MS);
  expect(posts).toEqual(["POST /api/assets/3/convert", "POST /api/assets/3/convert"]);
});

test("hover requests pending assets once, shared with the visibility request", () => {
  requestConversion(asset(4));
  requestConversion(asset(4));
  requestConversion(asset(5, "failed"));
  mount(asset(4));
  see(1);
  tick(VIEW_DWELL_MS);
  expect(posts).toEqual(["POST /api/assets/4/convert"]);
});
