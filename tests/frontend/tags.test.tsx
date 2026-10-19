import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AssetCard, type EditAsset } from "../../frontend/src/AssetCard";
import type { Asset } from "../../frontend/src/api";
import { matchesFilters, optimisticEdit } from "../../frontend/src/useAssets";

const asset = (over: Partial<Asset> = {}): Asset => ({
  id: 1, library_id: 1, path: "/l/crate.obj", name: "crate", ext: "obj", size: 1, mtime: 1, dcc_version: null,
  status: "ready", poly_count: 12, error: null, has_thumbnail: false, has_proxy: false, favorite: false, tags: [],
  ...over,
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

test("matchesFilters honours favourite and tag", () => {
  const a = asset({ favorite: true, tags: ["prop", "wood"] });
  expect(matchesFilters(a, { favorite: true, tag: "wood" })).toBe(true);
  expect(matchesFilters(asset(), { favorite: true })).toBe(false);
  expect(matchesFilters(a, { tag: "woo" })).toBe(false);
  expect(matchesFilters(asset(), {})).toBe(true);
});

test("optimisticEdit applies at once, then the server copy, and rolls back on error", async () => {
  const before = asset(), after = asset({ favorite: true }), saved = asset({ favorite: true, mtime: 2 });
  const seen: Asset[] = [];
  await expect(optimisticEdit(before, after, () => Promise.resolve(saved), (a) => seen.push(a))).resolves.toBe(saved);
  expect(seen).toEqual([after, saved]);

  seen.length = 0;
  const fail = optimisticEdit(before, after, () => Promise.reject(new Error("nope")), (a) => seen.push(a));
  await expect(fail).rejects.toThrow("nope");
  expect(seen).toEqual([after, before]);
});

function renderCard(a: Asset) {
  const edits: Parameters<EditAsset>[] = [];
  render(<AssetCard asset={a} apps={[]} onEdit={(...args) => edits.push(args)} onHoverStart={() => {}} onHoverEnd={() => {}} />);
  return edits;
}

test("star is a pressed-state toggle that sends PUT /favorite", async () => {
  const fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify(asset({ favorite: true })))));
  vi.stubGlobal("fetch", fetch);
  const edits = renderCard(asset());
  const star = screen.getByRole("button", { name: "Favourite crate" });
  expect(star.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(star);
  const [before, after, send] = edits[0];
  expect([before.favorite, after.favorite]).toEqual([false, true]);
  await send();
  expect(fetch).toHaveBeenCalledWith("/api/assets/1/favorite",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ favorite: true }) }));
});

test("tags are added normalized and sorted, and removed by their chip button", () => {
  const edits = renderCard(asset({ tags: ["metal", "wood"] }));
  const input = screen.getByRole("textbox", { name: "Add tag to crate" });
  fireEvent.change(input, { target: { value: "  Hero " } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(edits[0][1].tags).toEqual(["hero", "metal", "wood"]);
  expect((input as HTMLInputElement).value).toBe("");

  fireEvent.change(input, { target: { value: "METAL" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(edits).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "Remove tag metal" }));
  expect(edits[1][1].tags).toEqual(["wood"]);
});
