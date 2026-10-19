import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  findUpdate,
  isNewer,
  LATEST_RELEASE_URL,
  parseVersion,
  UPDATE_CHECK_KEY,
  UpdateNotice,
} from "../../frontend/src/UpdateNotice";

const calls: string[] = [];
let release: Response | Error;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  calls.length = 0;
  localStorage.clear();
  release = json({ tag_name: "v1.2.0", draft: false, prerelease: false });
  vi.stubGlobal("fetch", (url: string) => {
    calls.push(url);
    if (url === "/api/health") return Promise.resolve(json({ status: "ok", version: "1.1.0" }));
    return release instanceof Error ? Promise.reject(release) : Promise.resolve(release);
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("version compare is numeric per part and rejects non X.Y.Z", () => {
  expect(parseVersion("v1.10.0")).toEqual([1, 10, 0]);
  expect(parseVersion("1.2.0-rc1")).toBeNull();
  expect(parseVersion("latest")).toBeNull();
  expect(isNewer("v1.10.0", "1.9.9")).toBe(true);
  expect(isNewer("v2.0.0", "1.99.99")).toBe(true);
  expect(isNewer("v1.1.1", "1.1.0")).toBe(true);
  expect(isNewer("v1.1.0", "1.1.0")).toBe(false);
  expect(isNewer("v1.0.9", "1.1.0")).toBe(false);
  expect(isNewer("v1.2.0-rc1", "1.1.0")).toBe(false);
  expect(isNewer("v1.2.0", "dev")).toBe(false);
});

test("findUpdate ignores drafts, pre-releases, older tags and HTTP errors", async () => {
  expect(await findUpdate("1.1.0")).toEqual({
    version: "1.2.0",
    url: "https://github.com/g-s-jithesh/blendex/releases/tag/v1.2.0",
  });
  release = json({ tag_name: "v1.2.0", draft: false, prerelease: true });
  expect(await findUpdate("1.1.0")).toBeNull();
  release = json({ tag_name: "v1.2.0", draft: true, prerelease: false });
  expect(await findUpdate("1.1.0")).toBeNull();
  release = json({ tag_name: "v1.1.0", draft: false, prerelease: false });
  expect(await findUpdate("1.1.0")).toBeNull();
  release = json({ message: "API rate limit exceeded" }, 403);
  expect(await findUpdate("1.1.0")).toBeNull();
});

test("off by default: no request to GitHub", async () => {
  render(<UpdateNotice />);
  await act(async () => {});
  expect(calls).toEqual([]);
  expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
});

test("opted in: shows a dismissible banner that opens the release in a new window", async () => {
  localStorage.setItem(UPDATE_CHECK_KEY, "1");
  render(<UpdateNotice />);
  await act(async () => {});
  expect(calls).toEqual(["/api/health", LATEST_RELEASE_URL]);
  expect(screen.getByRole("status").textContent).toContain("Blendex 1.2.0 is available");
  const link = screen.getByRole("link");
  expect(link.getAttribute("href")).toBe("https://github.com/g-s-jithesh/blendex/releases/tag/v1.2.0");
  expect(link.getAttribute("target")).toBe("_blank");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(screen.queryByRole("status")).toBeNull();
});

test("turning the switch on checks and remembers it; offline stays silent", async () => {
  release = new TypeError("Failed to fetch");
  render(<UpdateNotice />);
  fireEvent.click(screen.getByRole("checkbox"));
  await act(async () => {});
  expect(localStorage.getItem(UPDATE_CHECK_KEY)).toBe("1");
  expect(calls).toEqual(["/api/health", LATEST_RELEASE_URL]);
  expect(screen.queryByRole("status")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(localStorage.getItem(UPDATE_CHECK_KEY)).toBe("0");
});
