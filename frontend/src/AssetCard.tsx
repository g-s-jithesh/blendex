import { memo, useState } from "react";
import { api, errorText, thumbnailUrl, type App, type Asset, type AssetStatus } from "./api";

const STATUS_LABEL: Record<AssetStatus, string> = {
  pending: "Pending",
  converting: "Converting…",
  ready: "No preview",
  failed: "Conversion failed",
  unsupported: "Unsupported",
};

export const appLabel = (app: App) => `Open in ${app.name}${app.version ? ` (v${app.version})` : ""}`;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

type Props = {
  asset: Asset;
  apps: App[];
  onHoverStart: (asset: Asset, el: HTMLElement) => void;
  onHoverEnd: (related: EventTarget | null) => void;
};

export const AssetCard = memo(function AssetCard({ asset, apps, onHoverStart, onHoverEnd }: Props) {
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const openers = apps.filter((a) => a.exts.includes(asset.ext));

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
      className="flex flex-col overflow-hidden rounded-md bg-neutral-900 ring-1 ring-neutral-800 hover:ring-neutral-600"
      onPointerEnter={(e) => onHoverStart(asset, e.currentTarget)}
      onPointerLeave={(e) => onHoverEnd(e.relatedTarget)}
    >
      <div data-thumb className="relative aspect-square bg-neutral-800">
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
        <div className="truncate font-medium text-neutral-100" title={asset.path}>{asset.name}</div>
        <div className="flex justify-between text-neutral-400">
          <span>.{asset.ext}{asset.dcc_version ? ` · v${asset.dcc_version}` : ""}</span>
          <span>{formatSize(asset.size)}</span>
        </div>
        {openers.length > 0 && (
          <select
            aria-label="Open in application"
            className="rounded bg-neutral-800 px-1 py-0.5 text-neutral-200"
            value=""
            onChange={(e) => {
              const app = openers.find((a) => a.id === e.target.value);
              if (app) launch(app);
            }}
          >
            <option value="" disabled>
              {openers.length === 1 ? appLabel(openers[0]) : "Open in…"}
            </option>
            {openers.map((a) => (
              <option key={a.id} value={a.id}>{appLabel(a)}</option>
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
