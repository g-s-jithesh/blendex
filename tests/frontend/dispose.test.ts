import {
  BoxGeometry, BufferGeometry, DataTexture, Float32BufferAttribute, Group, Line, LineBasicMaterial, Material,
  Mesh, MeshBasicMaterial, MeshStandardMaterial, Object3D, Points, PointsMaterial, Scene, SphereGeometry, Texture,
} from "three";
import { afterEach, expect, test, vi } from "vitest";
import { DRACOLoader } from "three/examples/jsm/loaders/DRACOLoader.js";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { applyLook, disposeProxy, loadProxy, prepareProxy, type Proxy3D } from "../../frontend/src/HoverViewport3D";

type Disposable = BufferGeometry | Material | Texture;

const tex = () => new DataTexture(new Uint8Array(16), 2, 2);

function buildScene() {
  const shared = tex();
  const created: Disposable[] = [];
  const add = <T extends Disposable>(x: T): T => (created.push(x), x);

  const standard = add(new MeshStandardMaterial({
    map: add(shared), normalMap: add(tex()), roughnessMap: add(tex()), emissiveMap: add(tex()),
  }));
  const box = new Mesh(add(new BoxGeometry(1, 2, 3)), standard);

  const multiGeo = add(new BoxGeometry(1, 1, 1));
  const multi = new Mesh(multiGeo, [
    add(new MeshBasicMaterial({ map: shared, alphaMap: add(tex()) })),
    add(new MeshStandardMaterial({ aoMap: add(tex()) })),
    add(new MeshBasicMaterial()),
  ]);
  multi.position.set(4, 0, 0);

  const sharedMat = new Mesh(add(new SphereGeometry(1, 8, 6)), standard);
  const line = new Line(
    add(new BufferGeometry().setAttribute("position", new Float32BufferAttribute([0, 0, 0, 1, 1, 1], 3))),
    add(new LineBasicMaterial()),
  );
  const points = new Points(
    add(new BufferGeometry().setAttribute("position", new Float32BufferAttribute([0, 0, 0], 3))),
    add(new PointsMaterial({ map: add(tex()) })),
  );

  const nested = new Group();
  nested.add(sharedMat, new Group().add(line, points));
  const root = new Group().add(box, multi, nested, new Object3D());
  const scene = new Scene().add(root);
  return { root, scene, created: [...new Set(created)] };
}

function reachable(p: Proxy3D): Set<Disposable> {
  const out = new Set<Disposable>();
  const addMaterial = (m: Material) => {
    out.add(m);
    for (const v of Object.values(m)) if (v instanceof Texture) out.add(v);
  };
  p.root.traverse((o) => {
    if (o instanceof Mesh || o instanceof Line || o instanceof Points) {
      out.add(o.geometry);
      [o.material].flat().forEach(addMaterial);
    }
  });
  p.meshes.flatMap(({ original }) => original).forEach(addMaterial);
  addMaterial(p.clay);
  return out;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("prepareProxy + applyLook + disposeProxy disposes every geometry, material and texture", () => {
  const { root, scene, created } = buildScene();
  const disposed = new Map<Disposable, number>();
  const spyOn = (proto: { dispose(): void }) => {
    const original = proto.dispose;
    vi.spyOn(proto, "dispose").mockImplementation(function (this: Disposable) {
      disposed.set(this, (disposed.get(this) ?? 0) + 1);
      original.call(this);
    });
  };
  spyOn(BufferGeometry.prototype);
  spyOn(Material.prototype);
  spyOn(Texture.prototype);

  const p = prepareProxy(root);
  expect(p.meshes).toHaveLength(3);
  expect(p.triangles).toBe(12 + 12 + 8 * 6 * 2 - 2 * 8);

  applyLook(p, true, true);
  for (const { mesh } of p.meshes) expect(mesh.material).toBe(p.clay);
  expect(p.clay.wireframe).toBe(true);
  applyLook(p, false, false);
  for (const { mesh, original } of p.meshes) expect(mesh.material).toBe(original);
  expect(p.clay.wireframe).toBe(false);

  const all = reachable(p);
  for (const c of created) expect(all.has(c)).toBe(true);
  expect(all.has(p.clay)).toBe(true);

  const fired = new Set<Disposable>();
  for (const d of all) d.addEventListener("dispose", () => fired.add(d));

  expect(disposed.size).toBe(0);
  disposeProxy(p);

  const missing = [...all].filter((d) => !disposed.has(d) || !fired.has(d)).map((d) => `${d.type}#${d.id}`);
  expect(missing).toEqual([]);
  expect(all.size).toBe(created.length + 1);
  expect(root.parent).toBeNull();
  expect(scene.children).toHaveLength(0);
});

test("disposeProxy still frees originals while clay is applied", () => {
  const { root, created } = buildScene();
  const p = prepareProxy(root);
  applyLook(p, true, false);
  const fired = new Set<Disposable>();
  for (const d of [...created, p.clay]) d.addEventListener("dispose", () => fired.add(d));
  disposeProxy(p);
  expect([...created, p.clay].filter((d) => !fired.has(d)).map((d) => `${d.type}#${d.id}`)).toEqual([]);
});

test("loadProxy shuts down its Draco decoder workers whether parsing succeeds or fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new ArrayBuffer(8))));
  const dispose = vi.spyOn(DRACOLoader.prototype, "dispose");
  const parse = vi.spyOn(GLTFLoader.prototype, "parseAsync").mockResolvedValueOnce({ scene: new Scene() } as unknown as GLTF);
  await loadProxy("/p.glb", new AbortController().signal);
  expect(dispose).toHaveBeenCalledTimes(1);
  parse.mockRejectedValueOnce(new Error("bad glb"));
  await expect(loadProxy("/p.glb", new AbortController().signal)).rejects.toThrow("bad glb");
  expect(dispose).toHaveBeenCalledTimes(2);
});
