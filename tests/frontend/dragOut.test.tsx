import { act, createEvent, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AssetCard } from "../../frontend/src/AssetCard";
import type { Asset } from "../../frontend/src/api";
import { DRAG_HINT } from "../../frontend/src/dragOut";

const asset: Asset = {
  id: 7, library_id: 1, path: "/l/crate.obj", name: "crate", ext: "obj", size: 1, mtime: 1, dcc_version: null,
  status: "ready", poly_count: 12, error: null, has_thumbnail: false, has_proxy: false, favorite: false, tags: [],
};

afterEach(() => {
  delete window.pywebview;
  document.body.innerHTML = "";
});

function renderCard() {
  const hoverEnds: unknown[] = [];
  const { container } = render(
    <AssetCard asset={asset} apps={[]} onEdit={() => {}} onHoverStart={() => {}} onHoverEnd={(r) => hoverEnds.push(r)} />,
  );
  return { thumb: container.querySelector<HTMLElement>("[data-thumb]")!, hoverEnds };
}

test("cards are not draggable outside the desktop app", () => {
  const { thumb } = renderCard();
  expect(thumb.draggable).toBe(false);
  expect(thumb.title).toBe("");
});

test("in the desktop app dragstart hands the asset id to drag_asset instead of an HTML drag", () => {
  const { thumb, hoverEnds } = renderCard();
  const drag_asset = vi.fn((_id: number) => Promise.resolve(null));
  act(() => {
    window.pywebview = { api: { drag_asset } };
    window.dispatchEvent(new Event("pywebviewready"));
  });
  expect(thumb.draggable).toBe(true);
  expect(thumb.title).toBe(DRAG_HINT);
  const start = createEvent.dragStart(thumb);
  fireEvent(thumb, start);
  expect(start.defaultPrevented).toBe(true);
  expect(drag_asset).toHaveBeenCalledWith(7);
  expect(hoverEnds).toEqual([null]);
});

test("a drag_asset error is shown on the card", async () => {
  window.pywebview = { api: { drag_asset: () => Promise.resolve("file not found: /l/crate.obj") } };
  const { thumb } = renderCard();
  expect(thumb.draggable).toBe(true);
  fireEvent.dragStart(thumb);
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "file not found: /l/crate.obj");
});
