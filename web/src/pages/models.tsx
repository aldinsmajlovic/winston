import { useState, type ReactNode } from 'react'
import { $api, type Config, type EngineConfig, type Preset } from '@/api/client'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { isRecord, setIn } from '@/lib/schema'

type Kind = 'llm' | 'tts' | 'stt'
type Choice = { value: string; label: string; hint?: string | null }

const CUSTOM = 'custom'
const OTHER = 'other'
const PATHS = [['llm'], ['tts'], ['stt'], ['engines', 'llm'], ['engines', 'tts']]

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

type ChoiceProps = { id: string; label: string; value: string; choices: Choice[]; onChange: (v: string) => void }

/** A dropdown of live choices with an Other entry that turns into a text field. */
function ChoiceField({ id, label, value, choices, onChange }: ChoiceProps) {
  const [typing, setTyping] = useState(false)
  const listed = choices.some((c) => c.value === value)
  const other = choices.length === 0 || typing || (!listed && value !== '')
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      {choices.length > 0 && (
        <Select
          value={other ? OTHER : value}
          onValueChange={(v) => {
            setTyping(v === OTHER)
            if (v !== OTHER) onChange(v)
          }}
        >
          <SelectTrigger id={id} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {choices.map((c) => (
              <SelectItem key={c.value} value={c.value}>
                {c.label}
              </SelectItem>
            ))}
            <SelectItem value={OTHER}>Other</SelectItem>
          </SelectContent>
        </Select>
      )}
      {other && (
        <Input id={choices.length ? `${id}-text` : id} value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  )
}

type SlotProps = { page: SettingsState; kind: Kind; title: string; presets: Preset[]; children?: ReactNode }

/** One slot: the engine or adapter as a dropdown of presets, then the slot's own controls. */
function SlotCard({ page, kind, title, presets, children }: SlotProps) {
  const current = matchPreset(kind, page.value, presets)
  const preset = presets.find((p) => p.id === current)
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor={`${kind}-engine`}>{kind === 'stt' ? 'Adapter' : 'Engine'}</Label>
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
                  {p.title}
                </SelectItem>
              ))}
              <SelectItem value={CUSTOM}>Custom</SelectItem>
            </SelectContent>
          </Select>
          {page.advanced && preset && <p className="text-sm text-muted-foreground">{preset.hint}</p>}
        </div>
        {children}
      </CardContent>
    </Card>
  )
}

export default function ModelsPage() {
  const s = useSettings(PATHS)
  const value = s.loading ? null : s.page.value
  const presets = $api.useQuery('get', '/api/presets')
  const models = $api.useQuery(
    'get',
    '/api/options/{name}',
    { params: { path: { name: 'llm_models' }, query: { base_url: value?.llm.base_url } } },
    { enabled: value !== null },
  )
  const voices = $api.useQuery(
    'get',
    '/api/options/{name}',
    { params: { path: { name: 'voices' }, query: { base_url: value?.tts?.base_url } } },
    { enabled: value !== null },
  )
  if (s.loading || !presets.data) return <PageSkeleton title="Models" />
  const { page } = s
  const cfg = page.value
  const set = (path: string[], v: unknown) => page.update((c) => setIn(c, path, v))
  return (
    <SettingsPage title="Models" page={page}>
      <SlotCard page={page} kind="llm" title="Brain" presets={presets.data.llm}>
        <ChoiceField
          id="llm-model"
          label="Model"
          value={cfg.llm.model}
          choices={models.data?.choices ?? []}
          onChange={(v) => set(['llm', 'model'], v)}
        />
        {page.advanced && (
          <>
            <SectionForm page={page} path={['llm']} omit={['model']} />
            {cfg.engines?.llm && <SectionForm page={page} path={['engines', 'llm']} driver={cfg.engines.llm.driver} />}
          </>
        )}
      </SlotCard>
      <SlotCard page={page} kind="tts" title="Voice" presets={presets.data.tts}>
        <ChoiceField
          id="tts-voice"
          label="Voice"
          value={cfg.tts?.voice ?? ''}
          choices={voices.data?.choices ?? []}
          onChange={(v) => set(['tts', 'voice'], v)}
        />
        {page.advanced && (
          <>
            <SectionForm page={page} path={['tts']} omit={['voice']} />
            {cfg.engines?.tts && <SectionForm page={page} path={['engines', 'tts']} driver={cfg.engines.tts.driver} />}
          </>
        )}
      </SlotCard>
      <SlotCard page={page} kind="stt" title="Hearing" presets={presets.data.stt}>
        {page.advanced && <SectionForm page={page} path={['stt']} omit={['provider']} />}
      </SlotCard>
    </SettingsPage>
  )
}
