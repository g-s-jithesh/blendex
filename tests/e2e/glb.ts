import { crc32, deflateSync } from "node:zlib";

export function png(size: number): Buffer {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const o = y * (size * 4 + 1) + 1 + x * 4;
      const on = ((x >> 3) + (y >> 3)) % 2 === 0;
      raw.set(on ? [230, 120, 40, 255] : [40, 90, 200, 255], o);
    }
  }
  const chunk = (type: string, data: Buffer) => {
    const head = Buffer.alloc(8);
    head.writeUInt32BE(data.length, 0);
    head.write(type, 4, "ascii");
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
    return Buffer.concat([head, data, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0)),
  ]);
}

const pad4 = (b: Buffer, fill: number) => Buffer.concat([b, Buffer.alloc((4 - (b.length % 4)) % 4, fill)]);

export function makeGlb(segments = 64, rings = 32): { glb: Buffer; triangles: number } {
  const pos: number[] = [], nrm: number[] = [], uv: number[] = [], idx: number[] = [];
  for (let r = 0; r <= rings; r++) {
    const v = r / rings, th = v * Math.PI;
    for (let s = 0; s <= segments; s++) {
      const u = s / segments, ph = u * Math.PI * 2;
      const n = [-Math.cos(ph) * Math.sin(th), Math.cos(th), Math.sin(ph) * Math.sin(th)];
      pos.push(...n);
      nrm.push(...n);
      uv.push(u, v);
    }
  }
  for (let r = 0; r < rings; r++) {
    for (let s = 0; s < segments; s++) {
      const a = r * (segments + 1) + s, b = a + segments + 1;
      idx.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  const parts = [
    Buffer.from(new Float32Array(pos).buffer), Buffer.from(new Float32Array(nrm).buffer),
    Buffer.from(new Float32Array(uv).buffer), Buffer.from(new Uint16Array(idx).buffer), png(64),
  ].map((b) => pad4(b, 0));
  let offset = 0;
  const bufferViews = parts.map((p) => ({ buffer: 0, byteOffset: (offset += p.length) - p.length, byteLength: p.length }));
  const count = pos.length / 3;
  const json = {
    asset: { version: "2.0", generator: "dam-e2e" },
    scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0, name: "sphere" }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1, TEXCOORD_0: 2 }, indices: 3, material: 0 }] }],
    materials: [{ pbrMetallicRoughness: { baseColorTexture: { index: 0 }, metallicFactor: 0 } }],
    textures: [{ source: 0, sampler: 0 }], samplers: [{}],
    images: [{ bufferView: 4, mimeType: "image/png" }],
    accessors: [
      { bufferView: 0, componentType: 5126, count, type: "VEC3", min: [-1, -1, -1], max: [1, 1, 1] },
      { bufferView: 1, componentType: 5126, count, type: "VEC3" },
      { bufferView: 2, componentType: 5126, count, type: "VEC2" },
      { bufferView: 3, componentType: 5123, count: idx.length, type: "SCALAR" },
    ],
    bufferViews: bufferViews.map((v, i) => (i < 3 ? { ...v, target: 34962 } : i === 3 ? { ...v, target: 34963 } : v)),
    buffers: [{ byteLength: offset }],
  };
  const jsonChunk = pad4(Buffer.from(JSON.stringify(json)), 0x20);
  const binChunk = Buffer.concat(parts);
  const chunkHead = (len: number, type: number) => {
    const h = Buffer.alloc(8);
    h.writeUInt32LE(len, 0);
    h.writeUInt32LE(type, 4);
    return h;
  };
  const total = 12 + 8 + jsonChunk.length + 8 + binChunk.length;
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(total, 8);
  const glb = Buffer.concat([
    header, chunkHead(jsonChunk.length, 0x4e4f534a), jsonChunk, chunkHead(binChunk.length, 0x004e4942), binChunk,
  ]);
  return { glb, triangles: idx.length / 3 };
}
