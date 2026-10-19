import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { QueueIndicator, queueText } from "../../frontend/src/QueueIndicator";

class FakeSocket {
  static last: FakeSocket | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor() {
    FakeSocket.last = this;
  }
  close() {}
}

const calls: string[] = [];
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url.endsWith("/pause")) return Promise.resolve(json({ queued: 340, running: 2, paused: true }));
    return Promise.resolve(json({ queued: 0, running: 0, paused: false }));
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("queueText", () => {
  expect(queueText({ queued: 0, running: 0, paused: false })).toBeNull();
  expect(queueText({ queued: 340, running: 2, paused: false })).toBe("Converting 2 · 340 queued");
  expect(queueText({ queued: 0, running: 1, paused: false })).toBe("Converting 1");
  expect(queueText({ queued: 340, running: 0, paused: true })).toBe("Paused · 340 queued");
});

test("hidden when idle, follows queue.updated events, pause button posts", async () => {
  render(<QueueIndicator />);
  await act(async () => {});
  expect(calls).toEqual(["GET /api/queue"]);
  expect(screen.getByRole("status").textContent).toBe("");
  expect(screen.queryByRole("button")).toBeNull();

  act(() => {
    FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type: "queue.updated", data: { queued: 340, running: 2, paused: false } }) });
  });
  expect(screen.getByRole("status").textContent).toBe("Converting 2 · 340 queued");

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
  });
  expect(calls).toContain("POST /api/queue/pause");
  expect(screen.getByRole("status").textContent).toBe("Paused · 340 queued");
  expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
});
