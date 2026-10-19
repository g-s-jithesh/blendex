import { expect, test } from "@playwright/test";

const asset = (id: number, ext: string) => ({
  id, library_id: 1, path: `C:/lib/model-${id}.${ext}`, name: `model-${id}`, ext, size: 10, mtime: 1_700_000_000,
  dcc_version: null, status: "ready", poly_count: 12, error: null, has_thumbnail: false, has_proxy: false, favorite: false, tags: [],
});
const ASSETS = [asset(1, "obj"), asset(2, "ma")];
const APPS = [
  { id: "blender-5.2", name: "Blender 5.2", version: "5.2", executable: "C:/B/blender.exe", exts: ["blend", "obj"] },
  { id: "explorer", name: "Explorer", version: null, executable: "C:/WINDOWS/explorer.exe", exts: ["blend", "ma", "obj"] },
];
const SESSION = { id: "blender-77", app: "Blender", version: "5.2", pid: 77, file: "C:\\proj\\house.blend", exts: ["blend", "obj"] };

test("a running Blender session shows up in the card menu and receives the import", async ({ page }) => {
  const imports: unknown[] = [];
  const launches: unknown[] = [];
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(m.text()); });
  await page.routeWebSocket("**/ws/events", () => {});
  await page.route("**/api/**", (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === "/api/libraries") return route.fulfill({ json: [{ id: 1, path: "C:/lib", created_at: 1 }] });
    if (path === "/api/apps") return route.fulfill({ json: APPS });
    if (path === "/api/bridges") return route.fulfill({ json: [{ app_id: "blender-5.2", name: "Blender 5.2", installed: true }] });
    if (path === "/api/assets") return route.fulfill({ json: { items: ASSETS, total: ASSETS.length } });
    if (path === "/api/queue") return route.fulfill({ json: { queued: 0, running: 0, paused: false } });
    if (path === "/api/sessions") return route.fulfill({ json: [SESSION] });
    if (path === "/api/tags") return route.fulfill({ json: [] });
    if (path === "/api/sessions/blender-77/import") {
      imports.push(req.postDataJSON());
      return route.fulfill({ json: { imported: ["Suzanne"] } });
    }
    if (path === "/api/launch") {
      launches.push(req.postDataJSON());
      return route.fulfill({ status: 202, body: "" });
    }
    return route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: path } } });
  });
  await page.goto("/");

  const objMenu = page.locator("[data-asset-id='1'] select");
  await objMenu.hover();
  await expect(objMenu.locator("option", { hasText: "Import into Blender 5.2 — house.blend" })).toHaveCount(1);
  await expect(objMenu.locator("option")).toHaveText(
    ["Open or import…", "Import into Blender 5.2 — house.blend", "Open in Blender 5.2 (v5.2)", "Open in Explorer"]);
  await objMenu.selectOption("session:blender-77");
  await expect(page.locator("[data-asset-id='1'] [role='status']")).toHaveText("Imported Suzanne into house.blend");
  expect(imports).toEqual([{ asset_id: 1 }]);

  const maMenu = page.locator("[data-asset-id='2'] select");
  await maMenu.hover();
  await expect(maMenu.locator("option")).toHaveText(["Open in Explorer", "Open in Explorer"]);
  await maMenu.selectOption("app:explorer");
  await expect.poll(() => launches).toEqual([{ asset_id: 2, app_id: "explorer" }]);

  await expect(page.getByRole("button", { name: /Install .* bridge/ })).toHaveCount(0);
  expect(problems).toEqual([]);
});
