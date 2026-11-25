import type { Config, Preset } from '@/api/client'
import { CUSTOM, matchPreset, type Kind } from '@/lib/presets'

type Points = number[][]
/** What /api/graphics-memory answers, as far as the bar reads it. */
export type MemoryPlan = {
  memory: 'graphics' | 'system'
  total_mb?: number
  other_mb?: number | null
  brain: { points?: Points | null; measured: boolean; share?: number | null }
  voices: Record<string, number>
  hearing: Record<string, number>
  green: number
  min_k: number
}

// Brains Winston runs on this computer: what the memory bar counts.
const LOCAL_BRAINS = ['llamacpp', 'llamacpp-amd', 'ollama', 'vllm']

/** MiB at k K tokens, on the line through the nearest two measured points (config/memory.py: at). */
export function at(points: Points, k: number): number {
  let i = points.findIndex(([pk]) => pk >= k)
  if (i < 0) i = points.length - 1
  i = Math.max(i, 1)
  const [[k0, v0], [k1, v1]] = [points[i - 1], points[i]]
  return v0 + ((v1 - v0) * (k - k0)) / (k1 - k0)
}

/** strength: how strongly a part takes the bar's colour; 0 is other programs, in grey. */
export type Part = { label: string; mib: number; strength: 0 | 1 | 2 | 3 | 4 }
export type Use = {
  total: number
  parts: Part[]
  used: number
  share: number
  tone: 'ok' | 'warn' | 'bad'
  /** The brain runs here, so the context slider moves the bar. */
  brainHere: boolean
  /** The brain runs here but its memory is not known (an unmeasured model whose size could not be read). */
  brainUnknown: boolean
  brainMeasured: boolean
  /** What other programs hold is not known (an AMD card while his engines run), so it is left out. */
  otherUnknown: boolean
  /** What goes wrong past the card: Ollama spills to the processor, the rest do not start. */
  spill: 'slow' | 'fail'
  /** MiB the brain takes at the minimum context, and at the draft's. */
  brainAt: (k: number) => number | null
}

function enabled(config: Config, kind: Kind): boolean {
  return config.engines?.[kind]?.enabled !== false
}

/** The draft's use of the card: other programs, then the brain (at the minimum context, then the more the
 * context slider adds), the voice and hearing on the card. */
export function memoryUse(plan: MemoryPlan, config: Config, presets: Record<Kind, Preset[]>): Use {
  const total = plan.total_mb ?? 0
  const llm = matchPreset('llm', config, presets.llm)
  const driver = config.engines?.llm?.driver
  const brainHere = enabled(config, 'llm') && (LOCAL_BRAINS.includes(llm) || (llm === CUSTOM && !!driver && driver !== 'external'))
  const points = plan.brain.points as Points | null | undefined
  const brainAt = (k: number): number | null =>
    !brainHere ? 0 : plan.brain.share ? plan.brain.share * total : points ? at(points, k) : null
  const k = (config.llm?.context ?? plan.min_k * 1024) / 1024
  const base = brainAt(plan.min_k) ?? 0
  const brain = brainAt(k) ?? 0
  const tts = enabled(config, 'tts') ? (plan.voices[matchPreset('tts', config, presets.tts)] ?? 0) : 0
  const stt = enabled(config, 'stt') ? (plan.hearing[matchPreset('stt', config, presets.stt)] ?? 0) : 0
  const parts: Part[] = [
    { label: 'Other programs', mib: plan.other_mb ?? 0, strength: 0 },
    { label: 'Brain', mib: base, strength: 4 },
    { label: 'More to keep in mind', mib: brain - base, strength: 3 },
    { label: 'Voice', mib: tts, strength: 2 },
    { label: 'Hearing', mib: stt, strength: 1 },
  ].filter((p): p is Part => p.mib > 1)
  const used = parts.reduce((n, p) => n + p.mib, 0)
  const share = total ? used / total : 0
  return {
    total,
    parts,
    used,
    share,
    tone: share > 1 ? 'bad' : share >= plan.green ? 'warn' : 'ok',
    brainHere,
    brainUnknown: brainHere && brainAt(plan.min_k) === null,
    brainMeasured: plan.brain.measured,
    otherUnknown: plan.other_mb === null,
    spill: llm === 'ollama' ? 'slow' : 'fail',
    brainAt,
  }
}

/** About how many exchanges a conversation holds at tokens: his instructions and tools take 10.5K, and an
 * exchange about 510 on average (eight real ones on 25 November; the news alone about 1,300). */
export function exchanges(tokens: number): number {
  const n = (tokens - 10518) / 510
  return n < 20 ? Math.max(0, Math.round(n)) : Math.round(n / 5) * 5
}

export const gb = (mib: number) => (mib / 1024).toFixed(1)
