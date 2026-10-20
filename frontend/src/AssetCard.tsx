import { memo, useRef, useState } from "react";
import { api, errorText, thumbnailUrl, type App, type Asset, type AssetStatus, type Session } from "./api";
import { requestConversion, useConvertOnView } from "./convertOnView";
import { DRAG_HINT, dragOut, useDragOut } from "./dragOut";
import { normalizeTag } from "./useAssets";

const STATUS_LABEL: Record<AssetStatus, string> = {
  pending: "Pending",
  converting: "Converting…",
  ready: "No preview",
  failed: "Conversion failed",
  unsupported: "Unsupported",
};

export const appLabel = (app: App) => `Open in ${app.name}${app.version ? ` (v${app.version})` : ""}`;
export const sessionFile = (s: Session) => s.file?.split(/[\\/]/).pop() || "untitled";
export const sessionLabel = (s: Session) => `Import into ${s.app}${s.version ? ` ${s.version}` : ""} — ${sessionFile(s)}`;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export type EditAsset = (before: Asset, after: Asset, send: () => Promise<Asset>) => void;

type Props = {
  asset: Asset;
  apps: App[];
  onEdit: EditAsset;
  onHoverStart: (asset: Asset, el: HTMLElement) => void;
  onHoverEnd: (related: EventTarget | null) => void;
};

export const AssetCard = memo(function AssetCard({ asset, apps, onEdit, onHoverStart, onHoverEnd }: Props) {
  const root = useRef<HTMLDivElement>(null);
  useConvertOnView(root, asset);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const [tagInput, setTagInput] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const draggable = useDragOut();
  const openers = apps.filter((a) => a.exts.includes(asset.ext));
  const importers = sessions.filter((s) => s.exts.includes(asset.ext));
  const refreshSessions = () => {
    api.listSessions().then(setSessions, () => setSessions([]));
  };

  const importInto = (s: Session) => {
    setNote({ text: `Importing into ${sessionFile(s)}…`, error: false });
    api.importInto(s.id, asset.id).then(
      ({ imported }) => setNote({ text: `Imported ${imported.join(", ") || "nothing"} into ${sessionFile(s)}`, error: false }),
      (e: unknown) => setNote({ text: errorText(e), error: true }),
    );
  };

  const toggleFavorite = () =>
    onEdit(asset, { ...asset, favorite: !asset.favorite }, () => api.setFavorite(asset.id, !asset.favorite));
  const saveTags = (tags: string[]) => onEdit(asset, { ...asset, tags }, () => api.setTags(asset.id, tags));
  const addTag = () => {
    const tag = normalizeTag(tagInput);
    setTagInput("");
    if (tag && !asset.tags.includes(tag)) saveTags([...asset.tags, tag].sort());
  };

  const launch = (app: App) => {
    setNote({ text: `Opening in ${app.name}…`, error: false });
    api.launch(asset.id, app.id).then(
      () => setNote({ text: `Opened in ${app.name}`, error: false }),
      (e: unknown) => setNote({ text: errorText(e), error: true }),
    );
  };

  return (
    <div
      data-asset-id={asset.id}
      className="flex flex-col overflow-hidden rounded-md bg-neutral-900 ring-1 ring-neutral-800 [contain-intrinsic-size:auto_180px_auto_280px] [content-visibility:auto] hover:ring-neutral-600"
      ref={root}
      onPointerEnter={(e) => {
        requestConversion(asset);
        onHoverStart(asset, e.currentTarget);
      }}
      onPointerLeave={(e) => onHoverEnd(e.relatedTarget)}
    >
      <div
        data-thumb
        className="relative aspect-square bg-neutral-800"
        draggable={draggable}
        title={draggable ? DRAG_HINT : undefined}
        onDragStart={(e) => {
          onHoverEnd(null);
          dragOut(e, asset.id, (text) => setNote({ text, error: true }));
        }}
      >
        {asset.has_thumbnail ? (
          <img
            src={thumbnailUrl(asset)}
            alt={asset.name}
            loading="lazy"
            draggable={false}
            className="h-full w-full object-contain"
          />
        ) : (
          <div
            data-status={asset.status}
            title={asset.error ?? undefined}
            className={`grid h-full place-items-center p-2 text-center text-xs ${
              asset.status === "failed" ? "text-red-400" : "text-neutral-400"
            } ${asset.status === "converting" ? "animate-pulse" : ""}`}
          >
            {STATUS_LABEL[asset.status]}
          </div>
        )}
        {asset.has_proxy && (
          <span className="absolute right-1 top-1 rounded bg-black/60 px-1 text-[10px] text-sky-300">3D</span>
        )}
      </div>
      <div className="flex flex-col gap-1 p-2 text-xs">
        <div className="flex items-center gap-1">
          <div className="min-w-0 flex-1 truncate font-medium text-neutral-100" title={asset.path}>{asset.name}</div>
          <button
            type="button"
            aria-pressed={asset.favorite}
            aria-label={`Favourite ${asset.name}`}
            title={asset.favorite ? "Remove from favourites" : "Add to favourites"}
            className={`rounded px-1 text-sm leading-none ${asset.favorite ? "text-amber-400" : "text-neutral-500 hover:text-neutral-200"}`}
            onClick={toggleFavorite}
          >
            {asset.favorite ? "★" : "☆"}
          </button>
        </div>
        <div className="flex justify-between text-neutral-400">
          <span>.{asset.ext}{asset.dcc_version ? ` · v${asset.dcc_version}` : ""}</span>
          <span>{formatSize(asset.size)}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {asset.tags.map((t) => (
            <span key={t} className="flex items-center gap-0.5 rounded bg-neutral-800 px-1 text-[10px] text-neutral-300">
              {t}
              <button
                type="button"
                aria-label={`Remove tag ${t}`}
                className="text-neutral-500 hover:text-neutral-100"
                onClick={() => saveTags(asset.tags.filter((x) => x !== t))}
              >
                ×
              </button>
            </span>
          ))}
          <input
            aria-label={`Add tag to ${asset.name}`}
            placeholder="+ tag"
            maxLength={40}
            className="min-w-12 flex-1 rounded bg-transparent px-1 text-[10px] text-neutral-300 placeholder:text-neutral-600 focus:bg-neutral-800"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) addTag();
            }}
          />
        </div>
        {(openers.length > 0 || importers.length > 0) && (
          <select
            aria-label="Open in application"
            className="rounded bg-neutral-800 px-1 py-0.5 text-neutral-200"
            value=""
            onPointerEnter={refreshSessions}
            onFocus={refreshSessions}
            onChange={(e) => {
              const session = importers.find((s) => `session:${s.id}` === e.target.value);
              const app = openers.find((a) => `app:${a.id}` === e.target.value);
              if (session) importInto(session);
              if (app) launch(app);
            }}
          >
            <option value="" disabled>
              {importers.length > 0 ? "Open or import…" : openers.length === 1 ? appLabel(openers[0]) : "Open in…"}
            </option>
            {importers.map((s) => (
              <option key={s.id} value={`session:${s.id}`}>{sessionLabel(s)}</option>
            ))}
            {openers.map((a) => (
              <option key={a.id} value={`app:${a.id}`}>{appLabel(a)}</option>
            ))}
          </select>
        )}
        {note && (
          <div role={note.error ? "alert" : "status"} className={note.error ? "text-red-400" : "text-neutral-400"}>
            {note.text}
          </div>
        )}
      </div>
    </div>
  );
});
