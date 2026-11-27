import type { Config, EngineConfig, Preset, Presets } from '@/api/client'
import { isRecord } from '@/lib/schema'

export type Kind = 'llm' | 'tts' | 'stt'

/** The slots and what their cards are called. */
export const SLOT_TITLES: Record<Kind, string> = { llm: 'Brain', tts: 'Speaking', stt: 'Hearing' }

export const CUSTOM = 'custom'

// What a preset needs, in words.
const NEEDS: Record<string, string> = {
  docker: 'Docker',
  nvidia: 'NVIDIA card',
  cuda: 'NVIDIA card',
  ollama: 'Ollama installed',
  amd: 'AMD graphics card',
  internet: 'Sends your requests out of your network',
}

/** A hint's first sentence. */
export const firstSentence = (hint: string) => hint.match(/^.*?[.!?](?=\s|$)/)?.[0] ?? hint

/** A preset's line under its title in the Runs on list: what it needs that the title does not already name
 * ("Docker" under "llama.cpp on NVIDIA (Docker)"), then its hint's first sentence. */
export function presetFacts(p: Preset): string {
  const needs = p.needs
    .map((n) => NEEDS[n] ?? n)
    .filter((n) => !p.title.toLowerCase().includes(n.split(' ')[0].toLowerCase()))
    .join(', ')
  return [needs, firstSentence(p.hint)].filter(Boolean).join('. ')
}

export type Way = NonNullable<Preset['way']>

// Runs on for a voice or hearing is where it runs, each with what you get, then what it needs; Model then
// picks among that way's presets, its first the recommended one.
const WAYS: Record<'tts' | 'stt', Record<Way, [title: string, hint: string]>> = {
  tts: {
    cpu: ['The processor', 'Leaves the graphics card free. Needs nothing more.'],
    gpu: ['The graphics card', 'The most natural voices, cloned from a clip. Needs an NVIDIA card.'],
    hosted: ['Hosted', "Nothing to run here. Needs the provider's key, and what he says leaves your network."],
  },
  stt: {
    cpu: ['The processor (built in)', 'Leaves the graphics card free. Needs nothing more.'],
    gpu: ['The graphics card', 'Hears the names he should hear right, given up front. Needs an NVIDIA card.'],
    hosted: ['Hosted', "Nothing to run here. Needs the provider's key, and your voice leaves your network."],
  },
}
const WAY_ICONS: Record<Way, string> = { cpu: 'cpu', gpu: 'nvidia', hosted: 'cloud' }

export type WayChoice = { way: Way; title: string; hint: string; icon: string; presets: Preset[] }

/** A voice's or hearing's ways to run, in the order their presets come; none for the brain, whose Runs on
 * picks the server and Model any model. A hosted way of one preset is called by that preset's title. */
export function waysOf(kind: Kind, presets: Preset[]): WayChoice[] {
  if (kind === 'llm') return []
  const out: WayChoice[] = []
  for (const p of presets) {
    if (!p.way) continue
    const w = out.find((x) => x.way === p.way)
    if (w) w.presets.push(p)
    else out.push({ way: p.way, title: WAYS[kind][p.way][0], hint: WAYS[kind][p.way][1], icon: WAY_ICONS[p.way], presets: [p] })
  }
  for (const w of out) if (w.way === 'hosted' && w.presets.length === 1) w.title = w.presets[0].title
  return out
}

function merge(base: unknown, over: unknown): unknown {
  if (!isRecord(base) || !isRecord(over)) return over
  const out: Record<string, unknown> = { ...base }
  for (const [k, v] of Object.entries(over)) out[k] = isRecord(v) && isRecord(out[k]) ? merge(out[k], v) : v
  return out
}

/** The draft with a preset's engine block and settings in place of the current ones. */
export function applyPreset(config: Config, kind: Kind, preset: Preset): Config {
  const engines: Record<string, EngineConfig> = { ...(config.engines ?? {}) }
  // A slot switched off stays off: picking where it runs does not start it.
  const was = config.engines?.[kind]
  if (preset.engine) engines[kind] = { ...(preset.engine as unknown as EngineConfig), ...(was?.enabled === false ? { enabled: false } : {}) }
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
    if ((e.url ?? null) !== (engine.url ?? null)) return false     // none matches none: a server on your network
    if (e.cwd && e.cwd !== engine.cwd) return false
    return true
  })
  return (hits.find((p) => slotSettings(p, kind).model === slot.model) ?? hits[0])?.id ?? CUSTOM
}

/** Slots whose preset the draft switches, each with the line a confirm says ("Hearing: Qwen3-ASR 0.6B on the graphics card")
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
