import type { Config, EngineConfig, Preset, Presets } from '@/api/client'
import { isRecord } from '@/lib/schema'

export type Kind = 'llm' | 'tts' | 'stt'

/** The slots and what their cards are called. */
export const SLOT_TITLES: Record<Kind, string> = { llm: 'Brain', tts: 'Speaking', stt: 'Hearing' }

export const CUSTOM = 'custom'

function merge(base: unknown, over: unknown): unknown {
  if (!isRecord(base) || !isRecord(over)) return over
  const out: Record<string, unknown> = { ...base }
  for (const [k, v] of Object.entries(over)) out[k] = isRecord(v) && isRecord(out[k]) ? merge(out[k], v) : v
  return out
}

/** The draft with a preset's engine block and settings in place of the current ones. */
export function applyPreset(config: Config, kind: Kind, preset: Preset): Config {
  const engines: Record<string, EngineConfig> = { ...(config.engines ?? {}) }
  if (preset.engine) engines[kind] = preset.engine as unknown as EngineConfig
  else delete engines[kind]
  return merge({ ...config, engines }, preset.settings) as Config
}

/** The settings a preset writes into its own section (llm, tts, stt). */
export function slotSettings(p: Preset, kind: Kind): Record<string, unknown> {
  const s = isRecord(p.settings) ? p.settings[kind] : undefined
  return isRecord(s) ? s : {}
}

/** Which preset the draft currently matches, by the fields that identify an engine. Hearing also needs its
 * adapter to match (Parakeet runs in-process, with no engine), and two presets on one engine (Qwen3-ASR's
 * sizes) are told apart by the model. */
export function matchPreset(kind: Kind, config: Config, presets: Preset[]): string {
  const engine = config.engines?.[kind]
  const slot: Record<string, unknown> = isRecord(config[kind]) ? config[kind] : {}
  const hits = presets.filter((p) => {
    const e = p.engine as Partial<EngineConfig> | null
    if (kind === 'stt' && slotSettings(p, kind).provider !== slot.provider) return false
    if (!e) return kind === 'stt' && !engine
    if (!engine || e.driver !== engine.driver) return false
    if (e.image && e.image !== engine.image) return false
    if (e.url && e.url !== engine.url) return false
    if (e.cwd && e.cwd !== engine.cwd) return false
    return true
  })
  return (hits.find((p) => slotSettings(p, kind).model === slot.model) ?? hits[0])?.id ?? CUSTOM
}

/** Slots whose preset the draft switches, each with the line a confirm says ("Hearing: Qwen3-ASR 0.6B on GPU")
 * and the paths that preset writes, so those are not listed field by field as well. */
export function presetSwitches(saved: Config, draft: Config, presets: Presets | undefined) {
  if (!presets) return []
  return (Object.keys(SLOT_TITLES) as Kind[]).flatMap((kind) => {
    const now = matchPreset(kind, draft, presets[kind])
    const p = presets[kind].find((x) => x.id === now)
    if (!p || now === matchPreset(kind, saved, presets[kind])) return []
    const paths = [['engines', kind], ...Object.keys(slotSettings(p, kind)).map((k) => [kind, k])]
    return [{ line: `${SLOT_TITLES[kind]}: ${p.title}`, paths }]
  })
}
