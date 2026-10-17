import { expect, test, type Page } from "@playwright/test";
import { makeGlb, png } from "./glb";

type Gpu = Map<WebGL2RenderingContext, { textures: Map<WebGLTexture, string>; buffers: Set<WebGLBuffer>; bound: WebGLTexture | null }>;
type Probe = { calls: number; contexts: WebGLRenderingContext[]; canvases: number; maxLive: number; gpu?: Gpu };
type DamDebug = { contexts: () => number; memory: () => { geometries: number; textures: number } | null };
type Win = Window & { __probe: Probe; __damDebug?: DamDebug; gc?: () => void };
type Point = { x: number; y: number };

const N = 20;
const SLOW_PROXY_MS = 1000;
const THUMB_PX = 32;
const { glb, triangles } = makeGlb();
const thumbPng = png(THUMB_PX);
const EXTS = ["blend", "obj", "blend", "fbx"] as const;

const asset = (id: number) => {
  const ext = EXTS[(id - 1) % EXTS.length];
  const name = `asset-${String(id).padStart(2, "0")}`;
  return {
    id, library_id: 1, path: `C:/lib/${name}.${ext}`, name, ext, size: 1024 * id, mtime: 1_700_000_000 + id * 37,
    dcc_version: ext === "blend" ? "5.2" : null, status: "ready", poly_count: null, error: null,
    has_thumbnail: true, has_proxy: ext !== "blend",
  };
};
const ASSETS = Array.from({ length: N }, (_, i) => asset(i + 1));
const PROXY_IDS = ASSETS.filter((a) => a.has_proxy).map((a) => a.id);
const BLEND_IDS = ASSETS.filter((a) => !a.has_proxy).map((a) => a.id);

function installProbe() {
  const probe: Probe = { calls: 0, contexts: [], canvases: 0, maxLive: 0 };
  (window as unknown as Win).__probe = probe;
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
    const ctx = (original as (...a: unknown[]) => RenderingContext | null).call(this, type, ...rest);
    if (type.startsWith("webgl")) {
      probe.calls++;
      const gl = ctx as WebGLRenderingContext | null;
      if (gl && !probe.contexts.includes(gl)) probe.contexts.push(gl);
      probe.maxLive = Math.max(probe.maxLive, probe.contexts.filter((c) => !c.isContextLost()).length);
    }
    return ctx;
  } as typeof HTMLCanvasElement.prototype.getContext;
  const gpu = new Map<WebGL2RenderingContext, { textures: Map<WebGLTexture, string>; buffers: Set<WebGLBuffer>; bound: WebGLTexture | null }>();
  probe.gpu = gpu;
  const slot = (gl: WebGL2RenderingContext) => {
    if (!gpu.has(gl)) gpu.set(gl, { textures: new Map(), buffers: new Set(), bound: null });
    return gpu.get(gl)!;
  };
  const P = WebGL2RenderingContext.prototype;
  const wrap = <K extends keyof WebGL2RenderingContext>(name: K, after: (gl: WebGL2RenderingContext, ret: unknown, args: unknown[]) => void) => {
    const fn = P[name] as (...a: unknown[]) => unknown;
    (P as unknown as Record<string, unknown>)[name] = function (this: WebGL2RenderingContext, ...args: unknown[]) {
      const ret = fn.apply(this, args);
      after(this, ret, args);
      return ret;
    };
  };
  wrap("createTexture", (gl, t) => t && slot(gl).textures.set(t as WebGLTexture, "?"));
  wrap("deleteTexture", (gl, _, [t]) => slot(gl).textures.delete(t as WebGLTexture));
  wrap("bindTexture", (gl, _, [, t]) => (slot(gl).bound = t as WebGLTexture | null));
  const label = (gl: WebGL2RenderingContext, w: unknown, h: unknown) => {
    const s = slot(gl);
    if (typeof w === "number" && s.bound && s.textures.get(s.bound) === "?") s.textures.set(s.bound, `${w}x${h}`);
  };
  wrap("texStorage2D", (gl, _, [, , , w, h]) => label(gl, w, h));
  wrap("texImage2D", (gl, _, [, , , w, h]) => label(gl, w, h));
  wrap("texImage3D", (gl, _, [, , , w, h]) => label(gl, w, h));
  wrap("createBuffer", (gl, b) => b && slot(gl).buffers.add(b as WebGLBuffer));
  wrap("deleteBuffer", (gl, _, [b]) => slot(gl).buffers.delete(b as WebGLBuffer));
  new MutationObserver((records) => {
    for (const r of records) {
      for (const n of r.addedNodes) {
        if (n instanceof Element) probe.canvases += (n.tagName === "CANVAS" ? 1 : 0) + n.querySelectorAll("canvas").length;
      }
    }
  }).observe(document, { childList: true, subtree: true });
}

async function setup(page: Page) {
  const log: string[] = [];
  const net = { proxyIds: [] as number[], thumbIds: [] as number[], proxyAborted: 0, badVersion: [] as string[] };
  page.on("console", (m) => log.push(`[${m.type()}] ${m.text()}`));
  page.on("pageerror", (e) => log.push(`[pageerror] ${e.message}`));
  page.on("requestfailed", (r) => {
    if (new URL(r.url()).pathname.endsWith("/proxy")) net.proxyAborted++;
  });
  await page.addInitScript(installProbe);
  await page.routeWebSocket("**/ws/events", () => {});
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const file = path.match(/^\/api\/assets\/(\d+)\/(proxy|thumbnail)$/);
    if (file) {
      const a = ASSETS.find((x) => x.id === Number(file[1]));
      if (!a || url.searchParams.get("v") !== String(a.mtime) || [...url.searchParams.keys()].length !== 1) {
        net.badVersion.push(url.href);
        return route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: url.href } } });
      }
      if (file[2] === "thumbnail") {
        net.thumbIds.push(a.id);
        return route.fulfill({ status: 200, contentType: "image/png", body: thumbPng });
      }
      net.proxyIds.push(a.id);
      if (!a.has_proxy) return route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: path } } });
      if (a.ext === "obj") await new Promise((r) => setTimeout(r, SLOW_PROXY_MS));
      await route.fulfill({ status: 200, contentType: "model/gltf-binary", body: glb }).catch(() => undefined);
      return;
    }
    if (path === "/api/libraries") return route.fulfill({ json: [{ id: 1, path: "C:/lib", created_at: 1 }] });
    if (path === "/api/apps") return route.fulfill({ json: [] });
    if (path === "/api/assets") return route.fulfill({ json: { items: ASSETS, total: N } });
    return route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: path } } });
  });
  await page.goto("/?debug=1");
  await expect(page.locator("[data-asset-id]")).toHaveCount(N);
  const centers: Point[] = [];
  let bottom = 0;
  for (let id = 1; id <= N; id++) {
    const box = (await page.locator(`[data-asset-id="${id}"] [data-thumb]`).boundingBox())!;
    const cardBox = (await page.locator(`[data-asset-id="${id}"]`).boundingBox())!;
    centers.push({ x: box.x + box.width / 2, y: box.y + box.height / 2 });
    bottom = Math.max(bottom, cardBox.y + cardBox.height);
  }
  const empty = { x: 10, y: bottom + 40 };
  expect(empty.y, "all 20 cards must fit in the viewport so no scroll is needed").toBeLessThan(1190);
  return { log, net, centers, empty };
}

const state = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as Win;
    return {
      contexts: w.__damDebug!.contexts(),
      memory: w.__damDebug!.memory(),
      canvases: document.querySelectorAll("canvas").length,
      viewports: document.querySelectorAll("[data-hover-viewport]").length,
    };
  });

const probe = (page: Page) =>
  page.evaluate(() => {
    const p = (window as unknown as Win).__probe;
    const undeletedTextures: Record<string, number> = {};
    for (const g of p.gpu!.values()) for (const t of g.textures.values()) undeletedTextures[t] = (undeletedTextures[t] ?? 0) + 1;
    const last = p.gpu!.get(p.contexts.at(-1) as WebGL2RenderingContext);
    return {
      getContextCalls: p.calls, uniqueContexts: p.contexts.length, canvasesMounted: p.canvases, maxLive: p.maxLive,
      notLost: p.contexts.filter((c) => !c.isContextLost()).length,
      undeletedTextures,
      undeletedBuffers: [...p.gpu!.values()].reduce((n, g) => n + g.buffers.size, 0),
      lastContextLut: [...(last?.textures.values() ?? [])].filter((t) => t === "16x16").length,
    };
  });

const dropProbeRefs = (page: Page) =>
  page.evaluate(() => {
    const p = (window as unknown as Win).__probe;
    p.contexts = [];
    p.gpu!.clear();
    p.calls = 0;
    p.canvases = 0;
  });

const heap = (page: Page) =>
  page.evaluate(async () => {
    const w = window as unknown as Win;
    for (let i = 0; i < 3; i++) {
      w.gc?.();
      await new Promise((r) => setTimeout(r, 50));
    }
    const mem = (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory;
    return { gcExposed: typeof w.gc === "function", usedJSHeapSize: mem?.usedJSHeapSize ?? null };
  });

async function expectReleased(page: Page) {
  await expect.poll(() => state(page), { timeout: 5000 }).toMatchObject({ contexts: 0, canvases: 0, viewports: 0 });
  // R3F force-loses the context again 500 ms after unmount; wait past it before checking counters and logs.
  await page.waitForTimeout(800);
  const p = await probe(page);
  const s = await state(page);
  expect(s, JSON.stringify(p)).toMatchObject({ contexts: 0, memory: { geometries: 0 }, canvases: 0, viewports: 0 });
  // three r182 never disposes its 1x1 placeholder textures or its shared 16x16 DFG LUT; info.memory still counts the LUT. Context loss frees both.
  expect(Object.keys(p.undeletedTextures).filter((t) => t !== "1x1" && t !== "16x16"), "asset textures must be deleted").toEqual([]);
  expect(p.undeletedBuffers, "asset buffers must be deleted").toBe(0);
  expect(p.lastContextLut).toBeLessThanOrEqual(1);
  expect(s.memory!.textures, JSON.stringify(p)).toBe(p.lastContextLut);
  expect(p.notLost, "every WebGL context created must be lost/released").toBe(0);
  expect(p.uniqueContexts, "exactly one WebGL context per mounted preview canvas").toBe(p.canvasesMounted);
  expect(p.maxLive, "never more than one live WebGL context").toBeLessThanOrEqual(1);
  return p;
}

const badLogs = (log: string[]) =>
  log.filter((l) => /^\[(error|warning|pageerror)\]/.test(l) || /CONTEXT_LOST|Too many active WebGL|Context Lost/i.test(l));

async function hoverUntilLoaded(page: Page, at: Point, leaveTo: Point) {
  await page.mouse.move(at.x, at.y);
  await page.waitForTimeout(2100);
  await expect.poll(async () => (await state(page)).memory?.geometries ?? 0).toBeGreaterThan(0);
  await page.mouse.move(leaveTo.x, leaveTo.y);
}

test("single hover mounts one context with GPU resources and releases all of it on leave", async ({ page }) => {
  const { log, net, centers, empty } = await setup(page);
  expect(await state(page)).toEqual({ contexts: 0, memory: null, canvases: 0, viewports: 0 });

  await page.mouse.move(centers[1].x, centers[1].y);
  await page.waitForTimeout(1900);
  expect((await state(page)).canvases).toBe(0);
  await page.waitForTimeout(200);
  await expect(page.locator("[data-hover-viewport] canvas")).toHaveCount(1);
  await expect.poll(async () => (await state(page)).memory?.geometries ?? 0).toBeGreaterThan(0);
  await expect.poll(async () => (await state(page)).memory?.textures ?? 0).toBeGreaterThan(0);
  await expect(page.getByTestId("polycount")).toHaveText(`${triangles.toLocaleString("en-US")} polys`);
  const loaded = await state(page);
  expect(loaded.contexts).toBe(1);
  const loadedProbe = await probe(page);
  expect(loadedProbe.undeletedTextures["64x64"]).toBe(1);
  expect(loadedProbe.undeletedBuffers).toBeGreaterThan(0);

  for (const key of ["c", "w", "c", "w"]) {
    await page.keyboard.press(key);
    await page.waitForTimeout(150);
  }
  expect((await state(page)).contexts).toBe(1);

  await page.mouse.move(empty.x, empty.y);
  const p = await expectReleased(page);
  expect(p.canvasesMounted).toBe(1);
  expect(p.uniqueContexts).toBe(1);
  console.log(JSON.stringify({ test: "single", whileLoaded: loaded, loadedProbe, probe: p, net, console: log }, null, 1));
  expect(net.proxyIds).toEqual([2]);
  expect(net.badVersion).toEqual([]);
  expect(badLogs(log)).toEqual([]);
});

test("blend cards are image-only: thumbnail shown, no 3D badge, no canvas, no context, no proxy fetch", async ({ page }) => {
  const { log, net, centers, empty } = await setup(page);
  for (const a of ASSETS) {
    const card = page.locator(`[data-asset-id="${a.id}"]`);
    await expect(card.getByText("3D", { exact: true }), `${a.ext} card ${a.id} badge`).toHaveCount(a.has_proxy ? 1 : 0);
    const img = card.locator("[data-thumb] img");
    await expect(img).toBeVisible();
    await expect(img).toHaveAttribute("src", `/api/assets/${a.id}/thumbnail?v=${a.mtime}`);
    await expect.poll(() => img.evaluate((i: HTMLImageElement) => (i.complete ? i.naturalWidth : 0))).toBe(THUMB_PX);
  }
  expect([...new Set(net.thumbIds)].sort((x, y) => x - y)).toEqual(ASSETS.map((a) => a.id));

  for (const id of [BLEND_IDS[0], BLEND_IDS.at(-1)!]) {
    const before = await probe(page);
    await page.mouse.move(centers[id - 1].x, centers[id - 1].y);
    await page.waitForTimeout(3000);
    const after = await probe(page);
    expect(await state(page), `blend ${id} after 3 s`).toEqual({ contexts: 0, memory: null, canvases: 0, viewports: 0 });
    expect(after.getContextCalls, "blend hover must not call getContext").toBe(before.getContextCalls);
    expect(after.canvasesMounted).toBe(0);
    await expect(page.locator(`[data-asset-id="${id}"] [data-thumb] img`)).toBeVisible();
    expect(net.proxyIds, "blend hover must not request /proxy").toEqual([]);
    await page.mouse.move(empty.x, empty.y);
  }
  expect((await probe(page)).getContextCalls).toBe(0);

  const fbx = ASSETS.find((a) => a.ext === "fbx")!;
  await page.mouse.move(centers[fbx.id - 1].x, centers[fbx.id - 1].y);
  await page.waitForTimeout(1900);
  expect((await state(page)).canvases).toBe(0);
  await page.waitForTimeout(200);
  await expect(page.locator("[data-hover-viewport] canvas")).toHaveCount(1);
  await expect.poll(async () => (await state(page)).memory?.geometries ?? 0).toBeGreaterThan(0);
  expect(net.proxyIds).toEqual([fbx.id]);
  await page.mouse.move(empty.x, empty.y);
  const p = await expectReleased(page);
  expect(p.canvasesMounted).toBe(1);
  expect(p.getContextCalls).toBe(1);
  console.log(JSON.stringify({ test: "blend-image-only", probe: p, net, console: log }, null, 1));
  expect(net.badVersion).toEqual([]);
  expect(badLogs(log)).toEqual([]);
});

test("rapid sweeps and mid-load leaves across 20 cards leak no contexts, geometries or textures", async ({ page }) => {
  const { log, net, centers, empty } = await setup(page);
  const heapCold = await heap(page);
  await hoverUntilLoaded(page, centers[PROXY_IDS[0] - 1], empty);
  await expectReleased(page);
  await dropProbeRefs(page);
  const heapWarm = await heap(page);
  let seed = 42;
  const dwell = () => 50 + ((seed = (seed * 1103515245 + 12345) % 2 ** 31) % 101);
  const rounds: (Awaited<ReturnType<typeof probe>> & { round: number; heap: number | null })[] = [];

  for (let round = 0; round < 3; round++) {
    const before = (await probe(page)).canvasesMounted;
    expect(before).toBe(0);
    const order = round % 2 ? [...centers].reverse() : centers;
    for (const c of order) {
      await page.mouse.move(c.x, c.y, { steps: 3 });
      await page.waitForTimeout(dwell());
    }
    await page.mouse.move(empty.x, empty.y, { steps: 3 });
    await page.waitForTimeout(300);
    expect((await probe(page)).canvasesMounted, `round ${round}: sweep must never mount a preview`).toBe(before);

    for (let i = 0; i < 10; i++) {
      const c = centers[PROXY_IDS[(round * 7 + i * 3) % PROXY_IDS.length] - 1];
      await page.mouse.move(c.x, c.y, { steps: 2 });
      await page.waitForTimeout(2100);
      await expect(page.locator("[data-hover-viewport] canvas")).toHaveCount(1);
      expect((await state(page)).contexts).toBeLessThanOrEqual(1);
      if (i % 3 === 0) {
        await expect.poll(async () => (await state(page)).memory?.geometries ?? 0).toBeGreaterThan(0);
        await page.mouse.move(empty.x, empty.y);
      } else if (i % 3 === 1) {
        await page.mouse.move(empty.x, empty.y);
      }
    }
    await page.mouse.move(empty.x, empty.y);
    const released = await expectReleased(page);
    expect(released.canvasesMounted).toBe(10);
    await dropProbeRefs(page);
    rounds.push({ round, ...released, heap: (await heap(page)).usedJSHeapSize });
  }

  expect(await state(page)).toEqual({ contexts: 0, memory: { geometries: 0, textures: rounds[2].lastContextLut }, canvases: 0, viewports: 0 });
  expect(await page.locator("canvas").count()).toBe(0);
  const warm = heapWarm.usedJSHeapSize;
  console.log(JSON.stringify({
    test: "stress", rounds, net, heapCold, heapWarm,
    heapDeltaPerRoundVsWarm: rounds.map((r) => (r.heap !== null && warm !== null ? r.heap - warm : null)),
    console: log,
  }, null, 1));
  expect(net.proxyAborted).toBeGreaterThan(0);
  expect(net.proxyIds.filter((id) => !PROXY_IDS.includes(id)), "no /proxy request for blend cards").toEqual([]);
  expect(net.badVersion).toEqual([]);
  expect(badLogs(log)).toEqual([]);
});
