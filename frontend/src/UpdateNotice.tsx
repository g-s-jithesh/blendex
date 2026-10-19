import { useEffect, useState } from "react";
import { api } from "./api";

export const UPDATE_CHECK_KEY = "blendex.checkUpdates";
export const LATEST_RELEASE_URL = "https://api.github.com/repos/g-s-jithesh/blendex/releases/latest";
const RELEASE_PAGE = "https://github.com/g-s-jithesh/blendex/releases/tag/";

type Release = { tag_name?: unknown; draft?: unknown; prerelease?: unknown };
export type Update = { version: string; url: string };

export function parseVersion(v: string): number[] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(v.trim());
  return m ? m.slice(1).map(Number) : null;
}

export function isNewer(candidate: string, current: string): boolean {
  const a = parseVersion(candidate);
  const b = parseVersion(current);
  if (!a || !b) return false;
  const i = a.findIndex((n, k) => n !== b[k]);
  return i >= 0 && a[i] > b[i];
}

export async function findUpdate(current: string): Promise<Update | null> {
  const res = await fetch(LATEST_RELEASE_URL, { headers: { Accept: "application/vnd.github+json" } });
  if (!res.ok) return null;
  const rel = (await res.json()) as Release;
  if (rel.draft || rel.prerelease || typeof rel.tag_name !== "string" || !isNewer(rel.tag_name, current)) return null;
  return { version: rel.tag_name.replace(/^v/, ""), url: RELEASE_PAGE + encodeURIComponent(rel.tag_name) };
}

function readOptIn(): boolean {
  try {
    return localStorage.getItem(UPDATE_CHECK_KEY) === "1";
  } catch {
    return false;
  }
}

export function UpdateNotice() {
  const [enabled, setEnabled] = useState(readOptIn);
  const [update, setUpdate] = useState<Update | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    api.health()
      .then((h) => findUpdate(h.version))
      .then((u) => {
        if (live) setUpdate(u);
      }, () => undefined);
    return () => {
      live = false;
    };
  }, [enabled]);

  const toggle = (on: boolean) => {
    setEnabled(on);
    if (!on) setUpdate(null);
    try {
      localStorage.setItem(UPDATE_CHECK_KEY, on ? "1" : "0");
    } catch {
      // Storage can be blocked; the switch then only lasts until the window closes.
    }
  };

  return (
    <>
      <label className="flex items-center gap-1 text-neutral-400" title="Asks GitHub for the latest Blendex release at startup">
        <input type="checkbox" checked={enabled} onChange={(e) => toggle(e.target.checked)} />
        Check for updates
      </label>
      {update && (
        <div role="status" className="flex basis-full items-center gap-3 rounded bg-sky-950 px-3 py-2 text-sky-200">
          <span>
            Blendex {update.version} is available.{" "}
            <a href={update.url} target="_blank" rel="noopener noreferrer" className="underline hover:text-white">
              Get it from GitHub
            </a>
          </span>
          <button type="button" className="ml-auto rounded px-2 hover:bg-sky-900" aria-label="Dismiss" onClick={() => setUpdate(null)}>
            ✕
          </button>
        </div>
      )}
    </>
  );
}
