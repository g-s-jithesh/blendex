export type Library = { id: number; path: string; created_at: number };
export type AssetStatus = "pending" | "converting" | "ready" | "failed" | "unsupported";
export type Asset = {
  id: number; library_id: number; path: string; name: string; ext: string;
  size: number; mtime: number; dcc_version: string | null;
  status: AssetStatus; poly_count: number | null; error: string | null;
  has_thumbnail: boolean; has_proxy: boolean; favorite: boolean; tags: string[];
};
export type TagCount = { tag: string; count: number };
export type App = { id: string; name: string; version: string | null; executable: string; exts: string[] };
export type Session = { id: string; app: string; version: string | null; pid: number; file: string | null; exts: string[] };
export type Bridge = { app_id: string; name: string; installed: boolean };
export type AssetPage = { items: Asset[]; total: number };
export type Health = { status: "ok"; version: string };

export type EventType =
  | "asset.added" | "asset.updated" | "asset.removed"
  | "conversion.started" | "conversion.done" | "conversion.failed";
export type AssetEvent = { type: EventType; asset_id: number; data: Asset };
export type QueueState = { queued: number; running: number; paused: boolean };
export type QueueEvent = { type: "queue.updated"; data: QueueState };
export type ServerEvent = AssetEvent | QueueEvent;

export type ErrorCode =
  | "VALIDATION_ERROR" | "INVALID_PATH" | "NOT_FOUND" | "ALREADY_EXISTS"
  | "UNSUPPORTED" | "LAUNCH_FAILED" | "BRIDGE_FAILED" | "INTERNAL";
export type ErrorEnvelope = { error: { code: ErrorCode; message: string } };

export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode | "HTTP_ERROR";
  constructor(status: number, code: ErrorCode | "HTTP_ERROR", message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export type AssetQuery = {
  library_id?: number; q?: string; ext?: string; favorite?: boolean; tag?: string; limit?: number; offset?: number;
};

export const API_BASE = "/api";

function isEnvelope(body: unknown): body is ErrorEnvelope {
  if (typeof body !== "object" || body === null || !("error" in body)) return false;
  const err = body.error;
  return typeof err === "object" && err !== null && "code" in err && "message" in err;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const text = await res.text();
  let body: unknown = undefined;
  try {
    body = text ? JSON.parse(text) : undefined;
  } catch {
    body = undefined;
  }
  if (!res.ok) {
    if (isEnvelope(body)) throw new ApiError(res.status, body.error.code, body.error.message);
    throw new ApiError(res.status, "HTTP_ERROR", `HTTP ${res.status} ${res.statusText}`.trim());
  }
  return body as T;
}

function queryString(params: AssetQuery): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const put = (body: unknown): RequestInit => ({ method: "PUT", body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  listLibraries: () => request<Library[]>("/libraries"),
  createLibrary: (path: string) => request<Library>("/libraries", post({ path })),
  deleteLibrary: (id: number) => request<void>(`/libraries/${id}`, { method: "DELETE" }),
  listAssets: (params: AssetQuery, signal?: AbortSignal) =>
    request<AssetPage>(`/assets${queryString(params)}`, { signal }),
  getAsset: (id: number) => request<Asset>(`/assets/${id}`),
  convertAsset: (id: number) => request<void>(`/assets/${id}/convert`, { method: "POST" }),
  setFavorite: (id: number, favorite: boolean) => request<Asset>(`/assets/${id}/favorite`, put({ favorite })),
  setTags: (id: number, tags: string[]) => request<Asset>(`/assets/${id}/tags`, put({ tags })),
  listTags: () => request<TagCount[]>("/tags"),
  getQueue: () => request<QueueState>("/queue"),
  pauseQueue: () => request<QueueState>("/queue/pause", { method: "POST" }),
  resumeQueue: () => request<QueueState>("/queue/resume", { method: "POST" }),
  listApps: () => request<App[]>("/apps"),
  addApp: (executable: string) => request<App>("/apps", post({ executable })),
  rescanApps: () => request<App[]>("/apps/rescan", { method: "POST" }),
  launch: (asset_id: number, app_id: string) => request<void>("/launch", post({ asset_id, app_id })),
  listSessions: () => request<Session[]>("/sessions"),
  importInto: (session_id: string, asset_id: number) =>
    request<{ imported: string[] }>(`/sessions/${encodeURIComponent(session_id)}/import`, post({ asset_id })),
  listBridges: () => request<Bridge[]>("/bridges"),
  installBridge: (app_id: string) => request<void>(`/bridges/${encodeURIComponent(app_id)}`, { method: "POST" }),
};

export const thumbnailUrl = (a: Asset) => `${API_BASE}/assets/${a.id}/thumbnail?v=${a.mtime}`;
export const proxyUrl = (a: Asset) => `${API_BASE}/assets/${a.id}/proxy?v=${a.mtime}`;

export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
