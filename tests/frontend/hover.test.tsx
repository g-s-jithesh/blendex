import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Asset } from "../../frontend/src/api";
import { HOVER_DELAY_MS, useHoverPreview } from "../../frontend/src/useHoverPreview";

const asset = (id: number, has_proxy = true): Asset => ({
  id, library_id: 1, path: `/l/a${id}.blend`, name: `a${id}`, ext: "blend", size: 1, mtime: 1,
  dcc_version: null, status: "ready", poly_count: 12, error: null, has_thumbnail: false, has_proxy, favorite: false, tags: [],
});

const card = () => document.body.appendChild(document.createElement("div"));

function setup() {
  const hook = renderHook(() => useHoverPreview());
  const enter = (a: Asset, el: HTMLElement = card()) => act(() => hook.result.current.enter(a, el));
  const leave = (related?: EventTarget | null) => act(() => hook.result.current.leave(related));
  const tick = (ms: number) => act(() => vi.advanceTimersByTime(ms));
  const active = () => hook.result.current.active;
  return { ...hook, enter, leave, tick, active };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

test("delay constant is 2000 ms", () => {
  expect(HOVER_DELAY_MS).toBe(2000);
});

test("no preview at 1999 ms, preview at 2000 ms", () => {
  const h = setup();
  const el = card();
  h.enter(asset(1), el);
  h.tick(1999);
  expect(h.active()).toBeNull();
  h.tick(1);
  expect(h.active()?.asset.id).toBe(1);
  expect(h.active()?.el).toBe(el);
  expect(h.active()?.signal.aborted).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
});

test("leave before 2000 ms clears the timer and never activates", () => {
  const h = setup();
  h.enter(asset(1));
  h.tick(1500);
  expect(vi.getTimerCount()).toBe(1);
  h.leave();
  expect(vi.getTimerCount()).toBe(0);
  h.tick(10_000);
  expect(h.active()).toBeNull();
});

test("leave after activation aborts the signal", () => {
  const h = setup();
  h.enter(asset(1));
  h.tick(2000);
  const signal = h.active()!.signal;
  h.leave(null);
  expect(signal.aborted).toBe(true);
  expect(h.active()).toBeNull();
});

test("entering card B cancels A's pending timer", () => {
  const h = setup();
  h.enter(asset(1));
  h.tick(1000);
  h.enter(asset(2));
  expect(vi.getTimerCount()).toBe(1);
  h.tick(1000);
  expect(h.active()).toBeNull();
  h.tick(1000);
  expect(h.active()?.asset.id).toBe(2);
});

test("entering card B aborts A's active preview", () => {
  const h = setup();
  h.enter(asset(1));
  h.tick(2000);
  const a = h.active()!.signal;
  h.enter(asset(2));
  expect(a.aborted).toBe(true);
  expect(h.active()).toBeNull();
  expect(vi.getTimerCount()).toBe(1);
  h.tick(2000);
  expect(h.active()?.asset.id).toBe(2);
});

test("asset without proxy never arms a timer", () => {
  const h = setup();
  h.enter(asset(1, false));
  expect(vi.getTimerCount()).toBe(0);
  h.tick(10_000);
  expect(h.active()).toBeNull();
});

test("scroll and resize cancel pending and active previews", () => {
  const h = setup();
  h.enter(asset(1));
  h.tick(500);
  act(() => document.body.dispatchEvent(new Event("scroll")));
  expect(vi.getTimerCount()).toBe(0);
  h.tick(5000);
  expect(h.active()).toBeNull();

  h.enter(asset(2));
  h.tick(2000);
  const signal = h.active()!.signal;
  act(() => window.dispatchEvent(new Event("resize")));
  expect(signal.aborted).toBe(true);
  expect(h.active()).toBeNull();
});

test("moving into the overlay or within the card is not a leave; no-arg leave always cancels", () => {
  const h = setup();
  const el = card();
  const inner = el.appendChild(document.createElement("span"));
  const overlay = document.body.appendChild(document.createElement("div"));
  overlay.setAttribute("data-hover-viewport", "");
  const inOverlay = overlay.appendChild(document.createElement("canvas"));
  h.enter(asset(1), el);
  h.tick(2000);
  h.leave(inOverlay);
  h.leave(inner);
  expect(h.active()?.signal.aborted).toBe(false);
  h.leave(document.body);
  expect(h.active()).toBeNull();

  h.enter(asset(2), el);
  h.leave();
  expect(vi.getTimerCount()).toBe(0);
});

test("unmount cancels a pending timer and aborts an active preview", () => {
  const h = setup();
  h.enter(asset(1));
  h.tick(2000);
  const signal = h.active()!.signal;
  h.unmount();
  expect(signal.aborted).toBe(true);

  const h2 = setup();
  h2.enter(asset(2));
  h2.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test("rapid enter/leave over 20 assets leaves zero pending timers and no preview", () => {
  const h = setup();
  const assets = Array.from({ length: 20 }, (_, i) => asset(i + 1));
  const els = assets.map(() => card());
  const signals: AbortSignal[] = [];
  let seed = 7;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) % 1900);
  for (let round = 0; round < 5; round++) {
    assets.forEach((a, i) => {
      h.enter(a, els[i]);
      expect(vi.getTimerCount()).toBeLessThanOrEqual(1);
      h.tick(rand());
      if (h.active()) signals.push(h.active()!.signal);
      if (i % 3 === 0) h.leave(document.body);
    });
    expect(h.active()).toBeNull();
    assets.forEach((a, i) => {
      h.enter(a, els[i]);
      h.tick(2000);
      signals.push(h.active()!.signal);
      if (i % 2) h.leave();
    });
  }
  h.leave();
  expect(vi.getTimerCount()).toBe(0);
  expect(h.active()).toBeNull();
  expect(signals.length).toBeGreaterThanOrEqual(100);
  expect(signals.every((s) => s.aborted)).toBe(true);
});
