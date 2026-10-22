// Winston's mark (docs/identity.md): a ring, and inside it a disc filled below a soft W and open above.
// Pure geometry and motion in a 100 by 100 viewBox, shared by Presence and the favicon script.

export type MarkState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'announcing'

/** The chosen shape. Depth and lift are in viewBox units. */
export const MARK = {
  radius: 46, // the ring's outer edge
  ring: 7, // the ring's width; the gap inside it is as wide
  depth: 11, // how far the W reaches
  span: 0.7, // share of the width the W takes; the line runs flat beyond it
  soft: 0.85, // 0 is straight segments, 1 is full curves
} as const

/** What the mark does in a frame: the W's depth, how bright the fields are, the travelling wave
 * (listening), the running light (thinking) and the lower field's glow (speaking). */
export type Frame = { depth: number; level: number; lift: number; wave: number; phase: number; glint: number; pos: number; glow: number }

export const REST: Frame = { depth: MARK.depth, level: 1, lift: 0, wave: 0, phase: 0, glint: 0, pos: 0, glow: 0 }

// Outer arms high, two valleys, a lower middle peak. Softness blends straight segments into curves.
const KNOTS = [-0.9, 0.84, 0.2, 0.84, -0.9]
function wY(t: number, depth: number, lift: number) {
  const curve = (1 - Math.cos(4 * Math.PI * t)) / 2 + 0.2 * Math.exp(-(((t - 0.5) / 0.1) ** 2)) - 0.9 * Math.abs(2 * t - 1) ** 2.5
  const f = Math.min(3.999, t * 4)
  const i = Math.floor(f)
  const tri = KNOTS[i] + (KNOTS[i + 1] - KNOTS[i]) * (f - i)
  return 50 + lift + depth * (tri + (curve - tri) * MARK.soft - 0.3)
}

/** The dividing line from edge to edge, as points. */
function line(frame: Frame, n = 120): [number, number][] {
  const xs: number[] = []
  const ys: number[] = []
  for (let i = 0; i <= n; i++) {
    const x = 4 + (92 * i) / n
    const t = Math.min(1, Math.max(0, 0.5 + (x - 50) / (92 * MARK.span)))
    xs.push(x)
    ys.push(wY(t, frame.depth, frame.lift))
  }
  // Round the joins between the W and the flat line.
  const rad = Math.round(2 + 4 * MARK.soft)
  return xs.map((x, i) => {
    let sum = 0
    let weights = 0
    for (let j = -rad; j <= rad; j++) {
      const w = Math.exp(-(j * j) / (rad * rad * 0.5))
      sum += ys[Math.min(n, Math.max(0, i + j))] * w
      weights += w
    }
    return [x, sum / weights + frame.wave * Math.sin(2 * Math.PI * (x / 46 - frame.phase))]
  })
}

const toPath = (points: [number, number][]) => points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join('')

/** The line itself (for the running light) and the upper field closed over it. */
export function paths(frame: Frame) {
  const d = toPath(line(frame))
  return { line: d, upper: `${d}L100 0L0 0Z` }
}

// One slow swell, 0 to 1 and back every 1.5 s, eased at both ends.
function swell(t: number) {
  const s = 0.5 - 0.5 * Math.cos((2 * Math.PI * t) / 1.5)
  return s * s * (3 - 2 * s)
}

/** Where each state is heading, t seconds after it began. */
export function target(state: MarkState, t: number, still: boolean): Frame {
  const d = MARK.depth
  switch (state) {
    case 'idle':
      return REST
    case 'listening': // the W settles and a gentle wave runs through the line, left to right
      return { ...REST, depth: d * 0.15, lift: -6, wave: 4, phase: still ? 0 : t * 0.45 }
    case 'thinking': // dimmed, a light runs along the line
      return { ...REST, level: 0.45, glint: 1, pos: still ? 0.5 : (t / 1.4) % 1 }
    case 'speaking': // the shape holds, the lower field glows
      return { ...REST, glow: 0.45 * (still ? 0.6 : swell(t)) }
    case 'announcing': // the W at its deepest, the whole mark pulsing
      return { ...REST, depth: d + 10, lift: -6, level: 0.6 + 0.4 * Math.cos(2 * Math.PI * (still ? 0 : (t % 1.6) / 1.6)) ** 2 }
  }
}

/** One step of easing from the current frame toward the target. The glow follows faster so its
 * swell keeps its shape; phase and position jump. */
export function ease(from: Frame, to: Frame, k: number): Frame {
  const step = (a: number, b: number, rate = k) => a + (b - a) * rate
  return {
    depth: step(from.depth, to.depth),
    level: step(from.level, to.level),
    lift: step(from.lift, to.lift),
    wave: step(from.wave, to.wave),
    glint: step(from.glint, to.glint),
    glow: step(from.glow, to.glow, Math.max(k, 0.35)),
    phase: to.wave ? to.phase : from.phase,
    pos: to.glint ? to.pos : from.pos,
  }
}
