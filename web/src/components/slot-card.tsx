import { useState, type ReactNode } from 'react'
import { $api, type Config, type EngineStatus, type Preset } from '@/api/client'
import { useErrorsUnder } from '@/api/field-errors'
import { engineLabel, isRunning, restartCost, STATE_WORDS, useEngineActions, useStatus } from '@/api/status'
import { Fold } from '@/components/fold'
import { KindIcon } from '@/components/kind-icon'
import { LogDialog } from '@/components/log-dialog'
import { SectionForm } from '@/components/section-form'
import { StatusLine } from '@/components/status-line'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useConfirm } from '@/hooks/use-confirm'
import { useDrivers } from '@/hooks/use-drivers'
import type { SettingsState } from '@/hooks/use-settings'
import { capitalize, since } from '@/lib/format'
import { applyPreset, CUSTOM, matchPreset, presetFacts, type Kind } from '@/lib/presets'
import { allBut, deepEqual, hasFields, isRecord, section, setIn } from '@/lib/schema'

type Props = { page: SettingsState; kind: Kind; title: string; presets: Preset[]; children?: ReactNode }

// Each slot's engine block in the config.
const ENGINES: Record<Kind, string[]> = { llm: ['engines', 'llm'], tts: ['engines', 'tts'], stt: ['engines', 'stt'] }

// The slot's own fields How it runs holds whatever runs it: facts about the server that Runs on sets.
const SERVER: Record<Kind, string[][]> = { llm: [['llm', 'thinking', 'control']], tts: [['tts', 'sample_rate']], stt: [] }

/** The slot's fields How it runs holds, by path: also the API address while Winston runs the engine
 * (Docker or Process), beside the port it must match. For a hosted server the address is the choice
 * itself, so it stays on the card. */
function held(kind: Kind, config: Config): string[][] {
  const driver = config.engines?.[kind]?.driver
  const local = driver === 'docker' || driver === 'subprocess'
  return [...(local ? [[kind, 'base_url']] : []), ...SERVER[kind]]
}

/** The keys of the slot's own section that its card leaves to How it runs, for the card form's omit. */
// eslint-disable-next-line react-refresh/only-export-components -- what the pages omit, beside the card that holds it
export function runsHere(kind: Kind, config: Config): string[] {
  return held(kind, config)
    .filter((p) => p.length === 2)
    .map((p) => p[1])
}

// What a slot switched off leaves him without.
const OFF: Record<Kind, string> = {
  llm: 'Off, so he cannot answer.',
  tts: 'Off, so he cannot speak aloud.',
  stt: 'Off, so he cannot hear you.',
}

const empty = (v: unknown) => v == null || (Array.isArray(v) && v.length === 0) || (isRecord(v) && Object.keys(v).length === 0)

/** Whether the draft runs the slot exactly as the preset does. A preset names only what it sets, so both
 * engine blocks are compared with the schema's defaults filled in and empty values left out. Whether it
 * starts with Winston is your choice, not how it runs. */
function runsAsPreset(page: SettingsState, kind: Kind, preset: Preset): boolean {
  const fields = section(page.schema, ENGINES[kind]).properties ?? {}
  const full = (engine: unknown) => {
    const e = isRecord(engine) ? engine : {}
    const keys = new Set([...Object.keys(fields), ...Object.keys(e)].filter((k) => k !== 'enabled'))
    return Object.fromEntries([...keys].map((k) => [k, e[k] ?? fields[k]?.default]).filter(([, v]) => !empty(v)))
  }
  return deepEqual(full(page.value.engines?.[kind]), full(preset.engine))
}

/** One slot (brain, speaking, hearing): what it runs on as a dropdown of presets, each with what it needs,
 * then the slot's own controls and, last, its closed How it runs. Picking a preset rewrites the engine
 * block and the settings that go with it, after asking when the engine block was changed by hand. */
export function SlotCard({ page, kind, title, presets, children }: Props) {
  const ask = useConfirm()
  const current = matchPreset(kind, page.value, presets)
  const preset = presets.find((p) => p.id === current)
  const choose = async (id: string) => {
    const p = presets.find((x) => x.id === id)
    if (!p) return
    const edited = preset ? !runsAsPreset(page, kind, preset) : !!page.value.engines?.[kind]
    if (edited) {
      const choice = await ask({
        title: 'Replace your changes to how it runs?',
        lines: [`${p.title} comes with its own way to run.`],
        choices: [{ label: 'Replace', value: 'yes', destructive: true }],
      })
      if (!choice) return
    }
    page.update((c) => applyPreset(c, kind, p))
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        <div className="grid gap-2">
          <Label htmlFor={`${kind}-engine`}>Runs on</Label>
          <Select value={current} onValueChange={(id) => void choose(id)}>
            <SelectTrigger id={`${kind}-engine`} className="w-full">
              {/* The title alone; the list below has a second line per preset. */}
              <SelectValue>
                {preset ? (
                  <>
                    <KindIcon name={preset.icon} />
                    {preset.title}
                  </>
                ) : (
                  'Custom'
                )}
              </SelectValue>
            </SelectTrigger>
            <SelectContent position="popper" className="max-w-(--radix-select-trigger-width)">
              {presets.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {/* The second line starts under the title, not under the icon. */}
                  <KindIcon name={p.icon} className="mt-0.5 self-start" />
                  <span className="flex flex-col gap-0.5">
                    {p.title}
                    <span className="text-xs text-muted-foreground">{presetFacts(p)}</span>
                  </span>
                </SelectItem>
              ))}
              {/* Only to name a config no preset matches; nothing to pick. */}
              {current === CUSTOM ? (
                <SelectItem value={CUSTOM} disabled>
                  Custom
                </SelectItem>
              ) : null}
            </SelectContent>
          </Select>
          <p className="text-sm text-muted-foreground">{preset?.hint ?? 'Your own setup; change it under How it runs below.'}</p>
          {page.value.engines?.[kind]?.enabled === false ? (
            <StatusLine
              tone="muted"
              action={{ label: 'Turn on', onClick: () => page.update((c) => setIn(c, ['engines', kind, 'enabled'], true)) }}
            >
              {OFF[kind]}
            </StatusLine>
          ) : null}
        </div>
        {children}
        <HowItRuns page={page} kind={kind} presets={presets} />
      </CardContent>
    </Card>
  )
}

const TONES = { healthy: 'ok', starting: 'muted', unhealthy: 'warning', stopped: 'muted', error: 'error' } as const

/** "Running, up 15 min", "Starting, 40 s of about 2 min" or "Failed: exited with 1". */
function stateText(e: EngineStatus): string {
  if (e.state === 'healthy') return `Running, up ${since(e.since)}`
  if (e.state === 'starting') return `Starting, ${since(e.since)} of ${restartCost(e)}`
  return e.detail ? `${STATE_WORDS[e.state]}: ${e.detail}` : STATE_WORDS[e.state]
}

/** The bottom of a slot card. While its engine is on but not Running, its state with Log and Restart or
 * Start, so a problem is never folded away. Then a closed How it runs, its driver beside it: open, the
 * state while Running, the engine form for its driver ("Reset to the preset" on the command line while
 * the block differs from the preset it matches), then the slot's fields it holds. It is there when the
 * slot has an engine block or one of those fields to draw, and an error from a refused save under it
 * opens it. */
function HowItRuns({ page, kind, presets }: Omit<Props, 'children' | 'title'>) {
  const status = useStatus()
  const actions = useEngineActions()
  const ask = useConfirm()
  const drivers = useDrivers()
  const [log, setLog] = useState(false)
  const lines = $api.useQuery('get', '/api/engines/{name}/log', { params: { path: { name: kind }, query: { lines: 300 } } }, { enabled: log })
  const paths = held(kind, page.value)
  const errors = useErrorsUnder([ENGINES[kind], ...paths])
  const block = page.value.engines?.[kind]
  // Each held field as its own one-field form on its section, when the draft draws it there.
  const fields = paths
    .map((p) => ({ key: p.join('.'), parent: p.slice(0, -1), omit: allBut(page.schema, p.slice(0, -1), p.slice(-1)) }))
    .filter(({ parent, omit }) => hasFields(section(page.schema, parent), page.schema, { omit, config: page.value, path: parent }))
  if (!block && !fields.length) return null
  const preset = presets.find((p) => p.id === matchPreset(kind, page.value, presets))
  const engine = block ? status.data?.engines.find((e) => e.name === kind) : undefined
  const byName = { params: { path: { name: kind } } }

  const restart = async (e: EngineStatus) => {
    const choice = await ask({
      title: `Restart ${engineLabel(kind)}?`,
      lines: [capitalize(restartCost(e))],
      choices: [{ label: 'Restart', value: 'yes' }],
    })
    if (choice) actions.restart.mutate(byName)
  }
  const stateLine = (e: EngineStatus) => (
    <StatusLine
      tone={TONES[e.state]}
      action={[
        { label: 'Log', onClick: () => setLog(true) },
        isRunning(e) ? { label: 'Restart', onClick: () => void restart(e) } : { label: 'Start', onClick: () => actions.start.mutate(byName) },
      ]}
    >
      {stateText(e)}
    </StatusLine>
  )
  // The preset's engine block only: its settings (the model, the voice) and Start with Winston stay as chosen.
  const reset =
    block && preset && !runsAsPreset(page, kind, preset) ? (
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => page.update((c) => applyPreset(c, kind, { ...preset, engine: { ...preset.engine, enabled: block.enabled }, settings: {} }))}
      >
        Reset to the preset
      </Button>
    ) : null
  return (
    <>
      {engine && engine.state !== 'healthy' && block?.enabled !== false ? stateLine(engine) : null}
      <Fold title="How it runs" summary={drivers.find((d) => d.value === block?.driver)?.label} forceOpen={errors}>
        <div className="flex flex-col gap-4 pt-2">
          {engine?.state === 'healthy' ? stateLine(engine) : null}
          {/* Each driver has one of these: arguments (Docker), a command (Process) or an address (External). */}
          {block ? <SectionForm page={page} path={ENGINES[kind]} adornments={reset ? { args: reset, cmd: reset, url: reset } : undefined} /> : null}
          {fields.map(({ key, parent, omit }) => (
            <SectionForm key={key} page={page} path={parent} omit={omit} />
          ))}
        </div>
      </Fold>
      <LogDialog title={`Log of ${engineLabel(kind)}`} open={log} onOpenChange={setLog} lines={lines.data?.lines} loading={lines.isLoading} />
    </>
  )
}
