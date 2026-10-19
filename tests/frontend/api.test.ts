import { expect, test } from "vitest";
import { proxyUrl, thumbnailUrl, type Asset } from "../../frontend/src/api";

const asset = (id: number, mtime: number): Asset => ({
  id, library_id: 1, path: `/l/a${id}.obj`, name: `a${id}`, ext: "obj", size: 1, mtime,
  dcc_version: null, status: "ready", poly_count: 12, error: null, has_thumbnail: true, has_proxy: true, favorite: false, tags: [],
});

test("thumbnailUrl and proxyUrl carry v=<mtime>", () => {
  expect(thumbnailUrl(asset(7, 1_700_000_123))).toBe("/api/assets/7/thumbnail?v=1700000123");
  expect(proxyUrl(asset(7, 1_700_000_123))).toBe("/api/assets/7/proxy?v=1700000123");
});

test("a new mtime changes both URLs and nothing else", () => {
  const before = asset(3, 100), after = asset(3, 101);
  expect(thumbnailUrl(after)).not.toBe(thumbnailUrl(before));
  expect(proxyUrl(after)).not.toBe(proxyUrl(before));
  for (const url of [thumbnailUrl(after), proxyUrl(after)]) {
    const u = new URL(url, "http://127.0.0.1:8765");
    expect([...u.searchParams.entries()]).toEqual([["v", "101"]]);
  }
});
