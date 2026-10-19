import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, errorText, type App as DccApp, type Bridge, type Library, type TagCount } from "./api";
import { AssetCard, type EditAsset } from "./AssetCard";
import { QueueIndicator } from "./QueueIndicator";
import { UpdateNotice } from "./UpdateNotice";
import { optimisticEdit, useAssets, type AssetFilters } from "./useAssets";
import { useHoverPreview } from "./useHoverPreview";

const HoverViewport3D = lazy(() => import("./HoverViewport3D"));

export const SEARCH_DEBOUNCE_MS = 250;

export function App() {
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [apps, setApps] = useState<DccApp[]>([]);
  const [libraryId, setLibraryId] = useState<number | undefined>(undefined);
  const [ext, setExt] = useState("");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [newPath, setNewPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [bridges, setBridges] = useState<Bridge[]>([]);
  const [bridgeNote, setBridgeNote] = useState<string | null>(null);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [tag, setTag] = useState("");
  const [tags, setTags] = useState<TagCount[]>([]);
  const [editError, setEditError] = useState<string | null>(null);

  const refreshTags = useCallback(() => {
    api.listTags().then(setTags, (e: unknown) => setError(errorText(e)));
  }, []);
  useEffect(refreshTags, [refreshTags]);

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qInput]);

  useEffect(() => {
    api.listLibraries().then(setLibraries, (e: unknown) => setError(errorText(e)));
    api.listApps().then(setApps, (e: unknown) => setError(errorText(e)));
    api.listBridges().then(setBridges, (e: unknown) => setError(errorText(e)));
  }, []);

  const installBridge = (b: Bridge) => {
    setBridgeNote(`Installing the bridge into ${b.name}…`);
    api.installBridge(b.app_id).then(
      () => {
        setBridges((all) => all.map((x) => (x.app_id === b.app_id ? { ...x, installed: true } : x)));
        setBridgeNote(`Bridge installed. Restart ${b.name} to import into it from here.`);
      },
      (e: unknown) => setBridgeNote(errorText(e)),
    );
  };

  const filters = useMemo<AssetFilters>(
    () => ({
      library_id: libraryId,
      q: q || undefined,
      ext: ext.trim().replace(/^\./, "").toLowerCase() || undefined,
      favorite: favoritesOnly || undefined,
      tag: tag || undefined,
    }),
    [libraryId, q, ext, favoritesOnly, tag],
  );
  const sentinel = useRef<HTMLDivElement>(null);
  const { page, error: assetError, loading, update } = useAssets(filters, sentinel);
  const editAsset = useCallback<EditAsset>(
    (before, after, send) => {
      optimisticEdit(before, after, send, update).then(
        () => {
          setEditError(null);
          refreshTags();
        },
        (e: unknown) => setEditError(errorText(e)),
      );
    },
    [update, refreshTags],
  );
  const hover = useHoverPreview();

  const knownExts = useMemo(() => [...new Set(["blend", ...apps.flatMap((a) => a.exts)])], [apps]);

  const addLibrary = (e: FormEvent) => {
    e.preventDefault();
    const path = newPath.trim();
    if (!path) return;
    api.createLibrary(path).then(
      (lib) => {
        setLibraries((ls) => [...ls, lib]);
        setNewPath("");
        setError(null);
      },
      (err: unknown) => setError(errorText(err)),
    );
  };

  const shownError = error ?? editError ?? assetError;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 flex flex-wrap items-center gap-2 border-b border-neutral-800 bg-neutral-950/95 px-4 py-3 text-sm backdrop-blur">
        <h1 className="mr-2 font-semibold">Blendex</h1>
        <select
          aria-label="Library"
          className="rounded bg-neutral-800 px-2 py-1"
          value={libraryId ?? ""}
          onChange={(e) => setLibraryId(e.target.value ? Number(e.target.value) : undefined)}
        >
          <option value="">All libraries</option>
          {libraries.map((l) => (
            <option key={l.id} value={l.id}>{l.path}</option>
          ))}
        </select>
        <form onSubmit={addLibrary} className="flex gap-1">
          <input
            aria-label="Library path"
            placeholder="Add library folder path…"
            className="w-64 rounded bg-neutral-800 px-2 py-1"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
          />
          <button type="submit" className="rounded bg-sky-600 px-2 py-1 hover:bg-sky-500">Add</button>
        </form>
        <input
          aria-label="Extension"
          placeholder="ext"
          list="known-exts"
          className="w-20 rounded bg-neutral-800 px-2 py-1"
          value={ext}
          onChange={(e) => setExt(e.target.value)}
        />
        <datalist id="known-exts">
          {knownExts.map((x) => <option key={x} value={x} />)}
        </datalist>
        <input
          aria-label="Search"
          type="search"
          placeholder="Search by name…"
          className="w-56 rounded bg-neutral-800 px-2 py-1"
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
        />
        <button
          type="button"
          aria-pressed={favoritesOnly}
          className={`rounded px-2 py-1 ${favoritesOnly ? "bg-amber-500 text-neutral-950" : "bg-neutral-800 hover:bg-neutral-700"}`}
          onClick={() => setFavoritesOnly((f) => !f)}
        >
          ★ Favourites
        </button>
        <select aria-label="Tag" className="rounded bg-neutral-800 px-2 py-1" value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">All tags</option>
          {tag && !tags.some((t) => t.tag === tag) && <option value={tag}>{tag} (0)</option>}
          {tags.map((t) => (
            <option key={t.tag} value={t.tag}>{t.tag} ({t.count})</option>
          ))}
        </select>
        {bridges.filter((b) => !b.installed).map((b) => (
          <button
            key={b.app_id}
            type="button"
            className="rounded bg-sky-700 px-2 py-1 text-white hover:bg-sky-600"
            title={`Lets you import assets into an open ${b.name} session`}
            onClick={() => installBridge(b)}
          >
            Install {b.name} bridge
          </button>
        ))}
        {bridgeNote && <span role="status" className="text-neutral-300">{bridgeNote}</span>}
        <QueueIndicator />
        <span className="ml-auto text-neutral-400">
          {loading ? "Loading…" : `${page.items.length} of ${page.total} assets`}
        </span>
        <UpdateNotice />
      </header>
      {shownError && (
        <div role="alert" className="mx-4 mt-3 rounded bg-red-950 px-3 py-2 text-sm text-red-300">{shownError}</div>
      )}
      <main className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 p-4">
        {page.items.map((a) => (
          <AssetCard key={a.id} asset={a} apps={apps} onEdit={editAsset} onHoverStart={hover.enter} onHoverEnd={hover.leave} />
        ))}
      </main>
      <div ref={sentinel} aria-hidden className="h-px" />
      {hover.active && (
        <Suspense fallback={null}>
          <HoverViewport3D preview={hover.active} onLeave={hover.leave} />
        </Suspense>
      )}
    </div>
  );
}
