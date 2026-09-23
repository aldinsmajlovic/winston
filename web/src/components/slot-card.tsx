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

/** Which preset the draft currently matches, by the fields that identify an engine. */
function matchPreset(kind: Kind, config: Config, presets: Preset[]): string {
  if (kind === 'stt') return presets.find((p) => p.id === config.stt?.provider)?.id ?? CUSTOM
  const engine = config.engines?.[kind]
  if (!engine) return CUSTOM
  const hit = presets.find((p) => {
    const e = p.engine as Partial<EngineConfig> | null
    if (!e || e.driver !== engine.driver) return false
    if (e.image && e.image !== engine.image) return false
    if (e.url && e.url !== engine.url) return false
    if (e.cwd && e.cwd !== engine.cwd) return false
    return true
  })
  return hit?.id ?? CUSTOM
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
            <Label htmlFor={`${kind}-engine`}>{kind === 'stt' ? 'Adapter' : 'Engine'}</Label>
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
