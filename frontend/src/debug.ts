import type { WebGLRenderer } from "three";

type Memory = { geometries: number; textures: number };
export type DamDebug = { contexts: () => number; memory: () => Memory | null };

declare global {
  interface Window {
    __damDebug?: DamDebug;
  }
}

const renderers = new Set<WebGLRenderer>();
let lastDisposed: WebGLRenderer | null = null;
let enabled = false;

export function trackRenderer(gl: WebGLRenderer): () => void {
  renderers.add(gl);
  return () => {
    renderers.delete(gl);
    if (enabled) lastDisposed = gl;
  };
}

export function installDebug(search: string = location.search): void {
  if (new URLSearchParams(search).get("debug") !== "1") return;
  enabled = true;
  window.__damDebug = {
    contexts: () => renderers.size,
    // A disposed renderer's counters still drop when its geometries/textures fire "dispose".
    memory: () => {
      const gl = renderers.values().next().value ?? lastDisposed;
      return gl ? { geometries: gl.info.memory.geometries, textures: gl.info.memory.textures } : null;
    },
  };
}
