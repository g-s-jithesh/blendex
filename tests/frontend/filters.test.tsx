import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import type { App, Asset } from "../../frontend/src/api";
import { BlenderBanner } from "../../frontend/src/BlenderBanner";
import { Combobox, type ComboOption } from "../../frontend/src/Combobox";

const EXTS: ComboOption[] = ["blend", "fbx", "glb", "obj"].map((x) => ({ value: x, label: x }));

function Harness({ freeText = true, initial = "", options = EXTS }: { freeText?: boolean; initial?: string; options?: ComboOption[] }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <Combobox label="Extension" allLabel="All types" value={value} options={options} onChange={setValue} freeText={freeText} />
      <output data-testid="value">{value}</output>
      <button type="button">outside</button>
    </>
  );
}

const input = () => screen.getByRole("combobox") as HTMLInputElement;
const optionTexts = () => screen.queryAllByRole("option").map((o) => o.textContent);
const value = () => screen.getByTestId("value").textContent;
const key = (k: string) => fireEvent.keyDown(input(), { key: k });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("focus selects the text and lists every option even when a filter is applied", () => {
  render(<Harness initial="obj" />);
  expect(input().getAttribute("aria-expanded")).toBe("false");
  fireEvent.focus(input());
  expect([input().selectionStart, input().selectionEnd]).toEqual([0, 3]);
  expect(input().getAttribute("aria-expanded")).toBe("true");
  expect(input().getAttribute("aria-controls")).toBe(screen.getByRole("listbox").id);
  expect(optionTexts()).toEqual(["All types", "blend", "fbx", "glb", "obj"]);
  expect(screen.getByRole("option", { selected: true }).textContent).toBe("obj");
});

test("typing narrows case-insensitively, Enter applies the first match, arrows move the highlight", () => {
  render(<Harness />);
  fireEvent.focus(input());
  fireEvent.change(input(), { target: { value: "L" } });
  expect(optionTexts()).toEqual(["blend", "glb"]);
  expect(value()).toBe("L");
  key("Enter");
  expect(value()).toBe("blend");
  expect(input().getAttribute("aria-expanded")).toBe("false");

  fireEvent.click(input());
  fireEvent.change(input(), { target: { value: "b" } });
  key("ArrowDown");
  key("ArrowDown");
  expect(input().getAttribute("aria-activedescendant")).toBe(screen.getByRole("option", { name: "glb" }).id);
  key("ArrowUp");
  key("Enter");
  expect(value()).toBe("fbx");
});

test("clicking an option applies it; free text not in the list still filters", () => {
  render(<Harness />);
  fireEvent.click(input());
  fireEvent.click(screen.getByRole("option", { name: "glb" }));
  expect(value()).toBe("glb");
  fireEvent.click(input());
  fireEvent.change(input(), { target: { value: "usdz" } });
  expect(screen.queryAllByRole("option")).toEqual([]);
  key("Enter");
  expect(value()).toBe("usdz");
});

test("Esc closes, a second Esc clears; the × button clears; blur closes", () => {
  render(<Harness initial="fbx" />);
  fireEvent.focus(input());
  key("Escape");
  expect(input().getAttribute("aria-expanded")).toBe("false");
  expect(value()).toBe("fbx");
  key("Escape");
  expect(value()).toBe("");

  fireEvent.click(input());
  fireEvent.click(screen.getByRole("option", { name: "obj" }));
  fireEvent.click(screen.getByRole("button", { name: "Clear extension filter" }));
  expect(value()).toBe("");

  fireEvent.click(input());
  expect(input().getAttribute("aria-expanded")).toBe("true");
  fireEvent.blur(input());
  expect(input().getAttribute("aria-expanded")).toBe("false");
});

test("tag mode: labels carry counts, typing only narrows, All clears, blur restores the applied tag", () => {
  const tags = [{ value: "metal", label: "metal (2)" }, { value: "wood", label: "wood (5)" }];
  render(<Harness freeText={false} options={tags} />);
  fireEvent.focus(input());
  expect(optionTexts()).toEqual(["All types", "metal (2)", "wood (5)"]);
  fireEvent.change(input(), { target: { value: "wo" } });
  expect(value()).toBe("");
  expect(optionTexts()).toEqual(["wood (5)"]);
  key("Enter");
  expect(value()).toBe("wood");
  expect(input().value).toBe("wood");

  fireEvent.focus(input());
  fireEvent.change(input(), { target: { value: "me" } });
  fireEvent.blur(input());
  expect(input().value).toBe("wood");
  fireEvent.click(input());
  fireEvent.click(screen.getByRole("option", { name: "All types" }));
  expect(value()).toBe("");
});

const asset = (id: number, status: Asset["status"], error: string | null = null): Asset => ({
  id, library_id: 1, path: `/l/a${id}.obj`, name: `a${id}`, ext: "obj", size: 1, mtime: 1, dcc_version: null,
  status, poly_count: null, error, has_thumbnail: false, has_proxy: false, favorite: false, tags: [],
});
const BLENDER: App = { id: "blender-5.2", name: "Blender 5.2", version: "5.2", executable: "C:/B/blender.exe", exts: ["blend"] };
const MAYA: App = { id: "maya-2027", name: "Maya 2027", version: "2027", executable: "C:/M/maya.exe", exts: ["ma"] };

function stubApi(routes: Record<string, () => Response>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const call = `${init?.method ?? "GET"} ${url}`;
    calls.push(init?.body ? `${call} ${String(init.body)}` : call);
    const route = routes[call];
    return Promise.resolve(route ? route() : new Response(null, { status: 202 }));
  });
  return calls;
}
const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });

const ASSETS = [
  asset(1, "failed", "Blender not found; install Blender or set DAM_BLENDER"),
  asset(2, "failed", "Maya not found; install Maya or set DAM_MAYAPY"),
  asset(3, "pending"),
];

test("banner: Set Blender path posts the executable, refreshes apps, and re-converts Blender-not-found cards", async () => {
  const calls = stubApi({ "POST /api/apps": json(BLENDER, 201), "GET /api/apps": json([BLENDER]) });
  const onApps = vi.fn();
  render(<BlenderBanner assets={ASSETS} onApps={onApps} />);
  expect(screen.getByText("Blender wasn't found — previews and Open in Blender need it.")).toBeTruthy();
  const field = screen.getByRole("textbox", { name: "Blender path" });
  expect(field.getAttribute("placeholder")).toBe("C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe or its folder");
  fireEvent.change(field, { target: { value: "  D:\\Blender\\blender.exe " } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Set Blender path" }));
  });
  expect(calls).toEqual([
    `POST /api/apps ${JSON.stringify({ executable: "D:\\Blender\\blender.exe" })}`,
    "GET /api/apps",
    "POST /api/assets/1/convert",
  ]);
  expect(onApps).toHaveBeenCalledWith([BLENDER]);
});

test("banner: INVALID_PATH shows its message inline; Rescan without Blender says so", async () => {
  const calls = stubApi({
    "POST /api/apps": json({ error: { code: "INVALID_PATH", message: "not a Blender or Maya executable" } }, 422),
    "POST /api/apps/rescan": json([MAYA]),
  });
  const onApps = vi.fn();
  render(<BlenderBanner assets={ASSETS} onApps={onApps} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Blender path" }), { target: { value: "C:/nope" } });
  await act(async () => {
    fireEvent.submit(screen.getByRole("textbox", { name: "Blender path" }));
  });
  expect(screen.getByRole("alert").textContent).toBe("not a Blender or Maya executable");
  expect(onApps).not.toHaveBeenCalled();

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Rescan" }));
  });
  expect(screen.getByRole("alert").textContent).toBe("Blender still wasn't found.");
  expect(onApps).toHaveBeenCalledWith([MAYA]);
  expect(calls.filter((c) => c.includes("/convert"))).toEqual([]);
});
