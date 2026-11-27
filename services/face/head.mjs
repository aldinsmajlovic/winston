// The low-poly heads in face.json, from three.js's facecap.glb (a scanned face with the 52 ARKit shapes):
// eyeballs, teeth and textures dropped; the left half simplified and mirrored onto the matching original
// vertices, so every kept vertex still carries its own shapes (left and right stay correct).
// Usage: node head.mjs facecap.glb page/face.json
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { weld, prune, compactPrimitive } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptSimplifier } from 'meshoptimizer';
import { writeFileSync } from 'fs';
await MeshoptDecoder.ready;
await MeshoptSimplifier.ready;
MeshoptSimplifier.useExperimentalFeatures = true;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
// Unpacked once: the file's compression and textures off.
const src = await io.read(process.argv[2]);
for (const t of src.getRoot().listTextures()) t.dispose();
for (const e of src.getRoot().listExtensionsUsed())
  if (['EXT_meshopt_compression', 'KHR_texture_basisu', 'KHR_texture_transform'].includes(e.extensionName)) e.dispose();
const full = await io.writeBinary(src), levels = {};
const LEVELS = { fewer: 160, medium: 260, more: 420 };  // head triangles, of 5,032
const PINS = { eye: 8, mouth: 12 };  // points kept on each eye and on the mouth
const MOTION = Number(process.env.MOTION ?? 0.5);  // weight of the talking and blinking movement against the resting shape

for (const [name, total] of Object.entries(LEVELS)) {
  const doc = await io.readBinary(full);
  for (const n of doc.getRoot().listNodes()) {
    const m = n.getMesh(); if (!m) continue;
    if (!m.listPrimitives()[0].listTargets().length) { n.setMesh(null); continue; }  // eyeballs and teeth: open holes instead
    for (const p of m.listPrimitives()) {
      for (const s of p.listSemantics()) if (s !== 'POSITION') p.setAttribute(s, null);
      for (const t of p.listTargets()) for (const s of t.listSemantics()) if (s !== 'POSITION') t.setAttribute(s, null);
    }
  }
  await doc.transform(prune(), weld());
  const head = doc.getRoot().listMeshes()[0].listPrimitives()[0];
  const P = Float32Array.from(head.getAttribute('POSITION').getArray()), n = P.length / 3;
  const I = Uint32Array.from(head.getIndices().getArray());
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) { lo = Math.min(lo, P[3 * i]); hi = Math.max(hi, P[3 * i]); }
  const cx = (lo + hi) / 2;
  const mirror = new Uint32Array(n);  // ponytail: O(n^2) nearest search, fine for 2,659 vertices once
  for (let i = 0; i < n; i++) {
    let best = Infinity;
    for (let j = 0; j < n; j++) {
      const dx = 2 * cx - P[3 * i] - P[3 * j], dy = P[3 * i + 1] - P[3 * j + 1], dz = P[3 * i + 2] - P[3 * j + 2], d = dx * dx + dy * dy + dz * dz;
      if (d < best) { best = d; mirror[i] = j; }
    }
  }
  const left = [];
  for (let t = 0; t < I.length; t += 3)
    if (P[3 * I[t]] + P[3 * I[t + 1]] + P[3 * I[t + 2]] <= 3 * cx) left.push(I[t], I[t + 1], I[t + 2]);
  // The openings (eyes, mouth, neck) are the loops of edges used by one triangle. Pin a few evenly spaced
  // points on the eyes and mouth so they keep their shape; the neck is left free.
  const lock = new Uint8Array(n), uses = new Map(), next = new Map();
  for (let t = 0; t < I.length; t += 3) for (const [a, b] of [[I[t], I[t + 1]], [I[t + 1], I[t + 2]], [I[t + 2], I[t]]]) {
    const k = Math.min(a, b) + ',' + Math.max(a, b); uses.set(k, (uses.get(k) || 0) + 1); next.set(a + ',' + b, b);
  }
  const loops = [], seen = new Set();
  for (const [k, c] of uses) {
    if (c !== 1) continue;
    let [a] = k.split(',').map(Number);
    if (seen.has(a)) continue;
    const loop = [];
    while (!seen.has(a)) { seen.add(a); loop.push(a); a = [...uses.keys()].map(e => e.split(',').map(Number)).find(([x, y]) => uses.get(x + ',' + y) === 1 && (x === a || y === a) && !seen.has(x === a ? y : x))?.find(v => v !== a) ?? a; }
    loops.push(loop);
  }
  const pin = (loop, k) => {
    const pts = loop.map(v => [P[3 * v], P[3 * v + 1], P[3 * v + 2]]);
    const len = pts.map((p, i) => Math.hypot(...p.map((c, j) => c - pts[(i + 1) % pts.length][j])));
    const L = len.reduce((a, b) => a + b, 0), start = loop.indexOf(loop.reduce((m, v) => P[3 * v] < P[3 * m] ? v : m));
    for (let q = 0; q < k; q++) {
      let d = q * L / k, i = start;
      while (d > len[i % loop.length]) { d -= len[i % loop.length]; i++; }
      lock[loop[i % loop.length]] = 1;
    }
    for (const v of loop) if (Math.abs(P[3 * v] - cx) < 1e-3 * (hi - lo)) lock[v] = 1;  // where the opening meets the centre line
  };
  // Of the openings: the two eyes sit off the centre line; the lips are the front one across it (the
  // other is the back of the mouth, unseen). The back of the head and the small holes in the ears stay free.
  const big = loops.filter(l => l.length >= 20 && l.length < Math.max(...loops.map(l => l.length)));
  const across = l => Math.min(...l.map(v => P[3 * v])) < cx && Math.max(...l.map(v => P[3 * v])) > cx;
  const depth = l => l.reduce((s, v) => s + P[3 * v + 1], 0) / l.length;
  for (const eye of big.filter(l => !across(l))) pin(eye, PINS.eye);
  pin(big.filter(across).reduce((a, b) => depth(a) > depth(b) ? a : b), PINS.mouth);
  // Weigh how far each vertex moves in the shapes that talk and blink, so the simplifier keeps the
  // vertices the animation needs (the lip outline, lids, mouth corners), not just the resting shape.
  const names = doc.getRoot().listMeshes()[0].getExtras().targetNames, targets = head.listTargets();
  const MOVES = ['jawOpen', 'mouthFunnel', 'mouthStretch_L', 'eyeBlink_L', 'mouthLowerDown_L'];
  const A = new Float32Array(n * MOVES.length * 3), extent = hi - lo;
  MOVES.forEach((m, k) => {
    const d = targets[names.indexOf(m)].getAttribute('POSITION').getArray();
    for (let i = 0; i < n; i++) for (let c = 0; c < 3; c++) A[(i * MOVES.length + k) * 3 + c] = d[3 * i + c] / extent;
  });
  const [half] = MeshoptSimplifier.simplifyWithAttributes(Uint32Array.from(left), P, 3, A, MOVES.length * 3,
    Array(MOVES.length * 3).fill(MOTION), lock, Math.round(total / 2) * 3, 1);
  const out = [...half];
  for (let t = 0; t < half.length; t += 3) out.push(mirror[half[t]], mirror[half[t + 2]], mirror[half[t + 1]]);
  head.getIndices().setArray(Uint32Array.from(out));
  compactPrimitive(head);
  for (const e of doc.getRoot().listExtensionsUsed()) e.dispose();
  await doc.transform(prune());
  const glb = await io.writeBinary(doc);
  levels[name] = Buffer.from(glb).toString('base64');
  console.log(name, 'triangles', out.length / 3, 'vertices', head.getAttribute('POSITION').getCount(), 'bytes', glb.length);
}
writeFileSync(process.argv[3], JSON.stringify(levels));
