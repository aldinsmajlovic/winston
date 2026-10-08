import type { ReactNode } from 'react'
import type { Config, EngineConfig, Preset } from '@/api/client'
import { InfoTip } from '@/components/info-tip'
import { KindIcon } from '@/components/kind-icon'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { SettingsState } from '@/hooks/use-settings'
import { isRecord } from '@/lib/schema'

export type Kind = 'llm' | 'tts' | 'stt'

const CUSTOM = 'custom'

function merge(base: unknown, over: unknown): unknown {
  if (!isRecord(base) || !isRecord(over)) return over
  const out: Record<string, unknown> = { ...base }
  for (const [k, v] of Object.entries(over)) out[k] = isRecord(v) && isRecord(out[k]) ? merge(out[k], v) : v
  return out
}

/** The draft with a preset's engine block and settings in place of the current ones. */
function applyPreset(config: Config, kind: Kind, preset: Preset): Config {
  const engines: Record<string, EngineConfig> = { ...(config.engines ?? {}) }
  if (preset.engine) engines[kind] = preset.engine as unknown as EngineConfig
  else delete engines[kind]
  return merge({ ...config, engines }, preset.settings) as Config
}

/** The settings a preset writes into its own section (llm, tts, stt). */
function slotSettings(p: Preset, kind: Kind): Record<string, unknown> {
  const s = isRecord(p.settings) ? p.settings[kind] : undefined
  return isRecord(s) ? s : {}
}

/** Which preset the draft currently matches, by the fields that identify an engine. Hearing also needs its
 * adapter to match (Parakeet runs in-process, with no engine), and two presets on one engine (Qwen3-ASR's
 * sizes) are told apart by the model. */
function matchPreset(kind: Kind, config: Config, presets: Preset[]): string {
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

type Props = { page: SettingsState; kind: Kind; title: string; presets: Preset[]; children?: ReactNode }

/** One slot (brain, speaking, hearing): what runs it as a dropdown of presets, then the slot's own
 * controls. Picking a preset rewrites the engine block and the settings that go with it. */
export function SlotCard({ page, kind, title, presets, children }: Props) {
  const current = matchPreset(kind, page.value, presets)
  const preset = presets.find((p) => p.id === current)
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={`${kind}-engine`}>Engine</Label>
            <InfoTip text={preset?.hint ?? 'Your own engine block; edit it under Advanced.'} />
          </div>
          <Select
            value={current}
            onValueChange={(id) => {
              const p = presets.find((x) => x.id === id)
              if (p) page.update((c) => applyPreset(c, kind, p))
            }}
          >
            <SelectTrigger id={`${kind}-engine`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {presets.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  <KindIcon name={p.icon} />
                  {p.title}
                </SelectItem>
              ))}
              <SelectItem value={CUSTOM}>Custom</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {children}
      </CardContent>
    </Card>
  )
}
