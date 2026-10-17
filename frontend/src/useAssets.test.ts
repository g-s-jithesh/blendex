import { expect, test } from "vitest";
import type { Asset, AssetEvent, EventType } from "./api";
import { applyEvent } from "./useAssets";

const asset = (id: number, name: string, ext = "blend"): Asset => ({
  id, library_id: 1, path: `/l/${name}.${ext}`, name, ext, size: 1, mtime: 1, dcc_version: null,
  status: "pending", poly_count: null, error: null, has_thumbnail: false, has_proxy: false,
});
const ev = (type: EventType, data: Asset): AssetEvent => ({ type, asset_id: data.id, data });

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
