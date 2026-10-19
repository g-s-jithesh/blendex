import { expect, test } from "@playwright/test";

const asset = (id: number) => ({
  id, library_id: 1, path: `C:/lib/model-${String(id).padStart(3, "0")}.obj`, name: `model-${String(id).padStart(3, "0")}`,
  ext: "obj", size: 10, mtime: 1_700_000_000, dcc_version: null, status: "ready", poly_count: 12, error: null,
  has_thumbnail: false, has_proxy: false, favorite: false, tags: [],
});

test("scrolling to the bottom pages in all 450 assets and live events keep the counter right", async ({ page }) => {
  const assets = Array.from({ length: 450 }, (_, i) => asset(i + 1));
  const offsets: string[] = [];
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(m.text()); });
  let push: (msg: string) => void = () => {};
  await page.routeWebSocket("**/ws/events", (ws) => { push = (msg) => ws.send(msg); });
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/assets") {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      offsets.push(String(offset));
      const items = assets.slice(offset, offset + Number(url.searchParams.get("limit") ?? 100));
      return route.fulfill({ json: { items, total: assets.length } });
    }
    if (path === "/api/libraries") return route.fulfill({ json: [{ id: 1, path: "C:/lib", created_at: 1 }] });
    if (["/api/apps", "/api/sessions", "/api/bridges", "/api/tags"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/queue") return route.fulfill({ json: { queued: 0, running: 0, paused: false } });
    return route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: path } } });
  });
  await page.goto("/");

  const counter = page.getByText(/^\d+ of \d+ assets$/);
  const cards = page.locator("[data-asset-id]");
  await expect(counter).toHaveText("200 of 450 assets");
  await expect(cards).toHaveCount(200);
  await expect(cards.first()).toHaveCSS("content-visibility", "auto");

  const late = { ...asset(451), name: "model-999", path: "C:/lib/model-999.obj" };
  assets.push(late);
  push(JSON.stringify({ type: "asset.added", asset_id: late.id, data: late }));
  await expect(counter).toHaveText("200 of 451 assets");

  await expect.poll(async () => {
    await page.mouse.wheel(0, 20_000);
    return counter.textContent();
  }, { timeout: 30_000 }).toBe("451 of 451 assets");
  await expect(cards).toHaveCount(451);
  await expect(cards.last()).toHaveAttribute("data-asset-id", "451");
  expect(offsets).toEqual(["0", "200", "400"]);
  expect(problems).toEqual([]);
});
