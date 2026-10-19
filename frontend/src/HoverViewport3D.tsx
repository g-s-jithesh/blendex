import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import {
  Box3, Line, Material, Mesh, MeshStandardMaterial, Object3D, PerspectiveCamera, Points, Sphere, Texture,
} from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/examples/jsm/loaders/DRACOLoader.js";
import { errorText, proxyUrl } from "./api";
import { trackRenderer } from "./debug";
import { VIEWPORT_ATTR, type ActivePreview } from "./useHoverPreview";

export type Proxy3D = {
  root: Object3D;
  meshes: { mesh: Mesh; original: Material | Material[] }[];
  clay: MeshStandardMaterial;
  triangles: number;
  radius: number;
};

export const PARSE_TIMEOUT_MS = 30_000;

export function prepareProxy(root: Object3D): Proxy3D {
  const meshes: Proxy3D["meshes"] = [];
  let triangles = 0;
  root.traverse((o) => {
    if (!(o instanceof Mesh)) return;
    meshes.push({ mesh: o, original: o.material });
    const g = o.geometry;
    triangles += Math.floor((g.index ? g.index.count : (g.attributes.position?.count ?? 0)) / 3);
  });
  const sphere = new Box3().setFromObject(root).getBoundingSphere(new Sphere());
  const radius = Number.isFinite(sphere.radius) && sphere.radius > 0 ? sphere.radius : 1;
  if (Number.isFinite(sphere.radius)) root.position.sub(sphere.center);
  const clay = new MeshStandardMaterial({ color: 0xbdbdbd, roughness: 0.85, metalness: 0 });
  return { root, meshes, clay, triangles, radius };
}

export async function loadProxy(url: string, signal: AbortSignal): Promise<Proxy3D> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`Proxy fetch failed: HTTP ${res.status}`);
  const data = await res.arrayBuffer();
  // three 0.182's DRACOLoader has no default decoder path; public/draco is three's own decoder, served offline.
  const draco = new DRACOLoader().setDecoderPath("/draco/");
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("Preview timed out while decoding the model")), PARSE_TIMEOUT_MS);
  });
  try {
    const gltf = await Promise.race([new GLTFLoader().setDRACOLoader(draco).parseAsync(data, ""), timeout]);
    return prepareProxy(gltf.scene);
  } finally {
    clearTimeout(timer);
    // A pooled loader keeps up to 4 decoder workers, each holding its own WASM heap, alive for the whole session.
    draco.dispose();
  }
}

export function applyLook(p: Proxy3D, clay: boolean, wireframe: boolean): void {
  for (const { mesh, original } of p.meshes) mesh.material = clay ? p.clay : original;
  for (const m of [p.clay, ...p.meshes.flatMap(({ original }) => original)]) {
    if ("wireframe" in m) m.wireframe = wireframe;
  }
}

function disposeMaterial(m: Material): void {
  for (const v of Object.values(m)) if (v instanceof Texture) v.dispose();
  m.dispose();
}

export function disposeProxy(p: Proxy3D): void {
  p.root.traverse((o) => {
    if (o instanceof Mesh || o instanceof Line || o instanceof Points) {
      o.geometry.dispose();
      [o.material].flat().forEach(disposeMaterial);
    }
  });
  p.meshes.flatMap(({ original }) => original).forEach(disposeMaterial);
  disposeMaterial(p.clay);
  p.root.removeFromParent();
}

function RendererLifecycle() {
  const gl = useThree((s) => s.gl);
  useEffect(() => {
    const untrack = trackRenderer(gl);
    return () => {
      untrack();
      gl.dispose();
      gl.forceContextLoss();
      // R3F calls forceContextLoss again 500 ms later; a second loseContext() logs a WebGL error.
      gl.forceContextLoss = () => undefined;
    };
  }, [gl]);
  return null;
}

function Frame({ radius }: { radius: number }) {
  const camera = useThree((s) => s.camera);
  useLayoutEffect(() => {
    if (!(camera instanceof PerspectiveCamera)) return;
    const d = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.1;
    camera.position.set(0.6, 0.45, 0.66).normalize().multiplyScalar(d);
    camera.near = d / 100;
    camera.far = d * 100;
    camera.updateProjectionMatrix();
  }, [camera, radius]);
  return null;
}

const toggleClass = (on: boolean) =>
  `rounded px-2 py-0.5 text-xs ${on ? "bg-sky-500 text-white" : "bg-neutral-800/80 text-neutral-200 hover:bg-neutral-700"}`;

export function HoverViewport3D({
  preview,
  onLeave,
}: {
  preview: ActivePreview;
  onLeave: (related: EventTarget | null) => void;
}) {
  const [proxy, setProxy] = useState<Proxy3D | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wireframe, setWireframe] = useState(false);
  const [clay, setClay] = useState(false);
  const { asset, el, signal } = preview;
  const url = proxyUrl(asset);
  const rect = useMemo(() => (el.querySelector("[data-thumb]") ?? el).getBoundingClientRect(), [el]);

  useEffect(() => {
    let loaded: Proxy3D | null = null;
    let live = true;
    setError(null);
    loadProxy(url, signal).then(
      (p) => {
        if (!live) return disposeProxy(p);
        loaded = p;
        setProxy(p);
      },
      (e: unknown) => {
        if (live && !signal.aborted) setError(errorText(e));
      },
    );
    return () => {
      live = false;
      if (loaded) disposeProxy(loaded);
      setProxy(null);
    };
  }, [url, signal]);

  useEffect(() => {
    if (proxy) applyLook(proxy, clay, wireframe);
  }, [proxy, clay, wireframe]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "w" || e.key === "W") setWireframe((v) => !v);
      if (e.key === "c" || e.key === "C") setClay((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const polys = asset.poly_count ?? proxy?.triangles ?? null;

  return (
    <div
      {...{ [VIEWPORT_ATTR]: "" }}
      className="fixed z-50 overflow-hidden rounded-md bg-neutral-900 shadow-2xl ring-1 ring-sky-500"
      style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
      onPointerLeave={(e) => onLeave(e.relatedTarget)}
    >
      <Canvas camera={{ fov: 45 }} dpr={[1, 2]}>
        <hemisphereLight args={[0xffffff, 0x444444, 1.2]} />
        <directionalLight position={[3, 5, 4]} intensity={2} />
        {proxy && <primitive object={proxy.root} dispose={null} />}
        {proxy && <Frame radius={proxy.radius} />}
        <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
        <RendererLifecycle />
      </Canvas>
      <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between gap-2 bg-black/50 px-2 py-1 text-xs">
        <span className="truncate" title={asset.name}>{asset.name}</span>
        <span data-testid="polycount" className="shrink-0 tabular-nums">
          {polys === null ? "…" : `${polys.toLocaleString()} polys`}
        </span>
      </div>
      <div className="absolute inset-x-0 bottom-0 flex gap-1 p-1">
        <button type="button" className={toggleClass(wireframe)} onClick={() => setWireframe((v) => !v)} title="W">
          Wireframe
        </button>
        <button type="button" className={toggleClass(clay)} onClick={() => setClay((v) => !v)} title="C">
          Clay
        </button>
      </div>
      {!proxy && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-xs text-neutral-400">
          {error ?? "Loading…"}
        </div>
      )}
    </div>
  );
}

export default HoverViewport3D;
