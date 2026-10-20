import { useState, type FormEvent } from "react";
import { api, errorText, type App, type Asset } from "./api";

export const hasBlender = (apps: App[]) => apps.some((a) => a.id.startsWith("blender"));
const retryable = (a: Asset) => a.status === "failed" && !!a.error?.startsWith("Blender not found");

export function BlenderBanner({ assets, onApps }: { assets: Asset[]; onApps: (apps: App[]) => void }) {
  const [path, setPath] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = (call: () => Promise<App[]>) => {
    setBusy(true);
    setNote(null);
    call().then(
      (apps) => {
        setBusy(false);
        if (hasBlender(apps)) assets.filter(retryable).forEach((a) => api.convertAsset(a.id).catch(() => undefined));
        else setNote("Blender still wasn't found.");
        onApps(apps);
      },
      (e: unknown) => {
        setBusy(false);
        setNote(errorText(e));
      },
    );
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const executable = path.trim();
    if (executable) run(() => api.addApp(executable).then(() => api.listApps()));
  };

  return (
    <form
      aria-label="Blender not found"
      onSubmit={submit}
      className="flex flex-wrap items-center gap-2 border-b border-amber-900/60 bg-amber-950/60 px-4 py-1.5 text-xs text-amber-100"
    >
      <span>Blender wasn't found — previews and Open in Blender need it.</span>
      <input
        aria-label="Blender path"
        placeholder="C:\Program Files\Blender Foundation\Blender 5.2\blender.exe or its folder"
        className="w-[30rem] max-w-full rounded bg-neutral-900 px-2 py-0.5 text-neutral-100"
        value={path}
        onChange={(e) => setPath(e.target.value)}
      />
      <button type="submit" disabled={busy || !path.trim()} className="rounded bg-sky-700 px-2 py-0.5 text-white hover:bg-sky-600 disabled:opacity-50">
        Set Blender path
      </button>
      <button type="button" disabled={busy} onClick={() => run(api.rescanApps)} className="rounded bg-neutral-800 px-2 py-0.5 hover:bg-neutral-700 disabled:opacity-50">
        Rescan
      </button>
      {note && <span role="alert" className="text-red-300">{note}</span>}
    </form>
  );
}
