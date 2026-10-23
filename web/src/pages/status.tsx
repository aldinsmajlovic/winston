import { useState, type ComponentProps, type ReactNode } from 'react'
import { Play, Plus, Square } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { $api, errorText, type EngineConfig, type EngineState, type EngineStatus, type ExtensionView } from '@/api/client'
import { useConfig } from '@/api/config'
import { engineLabel, isRunning, notWorking, partName, restartCost, STATE_WORDS, useEngineActions, useStatus } from '@/api/status'
import { ItemDialog } from '@/components/item-dialog'
import { KindIcon } from '@/components/kind-icon'
import { LogDialog } from '@/components/log-dialog'
import { PageSkeleton } from '@/components/page'
import { RowMenu } from '@/components/row-menu'
import { SectionForm } from '@/components/section-form'
import { DriverLabel, StateIcon } from '@/components/state-icon'
import { StatusLine } from '@/components/status-line'
import { DataTable, type Column } from '@/components/templates/list-page'
import { SettingsPage } from '@/components/templates/settings-page'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useConfirm } from '@/hooks/use-confirm'
import { useDrivers } from '@/hooks/use-drivers'
import { useSettings } from '@/hooks/use-settings'
import { capitalize, since } from '@/lib/format'
import { matchPreset, SLOT_TITLES, type Kind } from '@/lib/presets'
import { getIn, section, setIn } from '@/lib/schema'
import { SETUP_STEPS } from '@/lib/setup'
import { cn } from '@/lib/utils'

const CONTROL = ['control']
const SLOTS = Object.keys(SLOT_TITLES)
const ENGINE = /^[a-z][a-z0-9_-]{0,39}$/ // a custom engine's name: its page's address and its container's name
const LOCAL = ['127.0.0.1', 'localhost', '::1']

type Driver = EngineConfig['driver']
type Adding = { name: string; driver: Driver }
type Tone = ComponentProps<typeof StatusLine>['tone']

/** One row of his parts: its role, what runs it, its state and the page that sets it up. */
type Part = {
  key: string
  icon?: string | null
  role: string
  runs: string
  to: string
  /** The process Winston runs for it: its state, Start or Restart, Stop and Log. */
  process?: EngineStatus
  /** A state that is no process of Winston's (Home Assistant answering), with its own word. */
  state?: { state: EngineState; detail?: string; word?: string }
  off?: boolean
  /** The switch that turns it on: while off, its command is Turn on, in the draft. */
  enable?: string[]
  /** Off until it is set up there (Discord): its command is Set up. */
  setUp?: boolean
}

const TONES: Record<EngineState, Tone> = { healthy: 'ok', installing: 'muted', starting: 'muted', unhealthy: 'warning', stopped: 'muted', error: 'error' }

// What stops while a part is down, and how its return reads, for its Stop and Restart confirms.
const DOWN: Record<string, [string, string]> = {
  llm: ['He cannot answer anywhere', 'it starts'],
  tts: ['He cannot speak aloud', 'it starts'],
  stt: ['He cannot hear you', 'it starts'],
  voice: ['The Talk page microphone does not work', 'it starts'],
  satellite: ['The room boxes stop listening', 'they start'],
  discord: ['He does not answer on Discord', 'it starts'],
}
const downOf = (name: string) => DOWN[name] ?? ['What uses it stops working', 'it starts']

/** ", up 15 min", ", 40 s of about 2 min", ": the model, 6.2 of 16.4 GB" or " for 3 h": how long it has been in
 * its state, or how far an install or a first download has got. */
function stateTime(e: EngineStatus): string {
  const t = since(e.since)
  if (e.state === 'healthy') return `, up ${t}`
  if (e.state === 'installing') return e.detail ? `: ${e.detail}` : ` for ${t}`
  if (e.state === 'starting') return e.detail ? `: ${e.detail}` : `, ${t} of ${restartCost(e)}`
  return ` for ${t}`
}

/** ", waits for a restart" while it runs on older settings than the saved ones (the header's Restart). */
const waits = (e: EngineStatus) => (e.config_changed && isRunning(e) ? ', waits for a restart' : '')

/** "Discord failed: no token", or the sentence alone when there is no reason. */
const reason = (text: string, why?: string | null) => (why ? `${text}: ${why}` : `${text}.`)

/** The brain's usual port is 8000; a new engine takes the next port above every one in use. */
const nextPort = (engines: Record<string, EngineConfig>) => Math.max(7999, ...Object.values(engines).map((e) => e.port ?? 0)) + 1

const needsYou = (x: ExtensionView) => x.enabled && ((x.needs?.length ?? 0) > 0 || notWorking(x))

/** The end of an engine's or channel's log, fetched while it is open. */
function EngineLog({ name, open, onOpenChange }: { name: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const lines = $api.useQuery('get', '/api/engines/{name}/log', { params: { path: { name }, query: { lines: 300 } } }, { enabled: open })
  return <LogDialog title={`Log of ${engineLabel(name)}`} open={open} onOpenChange={onOpenChange} lines={lines.data?.lines} loading={lines.isLoading} />
}

/** Start, Stop, Restart and Log for an engine or channel. Stop and Restart confirm what stops, by its role. */
function useCommands() {
  const actions = useEngineActions()
  const ask = useConfirm()
  const [log, setLog] = useState({ name: '', open: false })
  const at = (e: EngineStatus) => ({ params: { path: { name: e.name } } })
  return {
    start: (e: EngineStatus) => actions.start.mutate(at(e)),
    restart: async (e: EngineStatus) => {
      const [effect] = downOf(e.name)
      const lines = [`${effect} for ${restartCost(e)}.`]
      if (await ask({ title: `Restart ${engineLabel(e)}?`, lines, choices: [{ label: 'Restart', value: 'yes' }] })) actions.restart.mutate(at(e))
    },
    stop: async (e: EngineStatus) => {
      const [effect, until] = downOf(e.name)
      const lines = [`${effect} until ${until} again, ${restartCost(e)}.`]
      if (await ask({ title: `Stop ${engineLabel(e)}?`, lines, choices: [{ label: 'Stop', value: 'yes', destructive: true }] })) actions.stop.mutate(at(e))
    },
    showLog: (e: EngineStatus) => setLog({ name: e.name, open: true }),
    log: { ...log, onOpenChange: (open: boolean) => setLog((l) => ({ ...l, open })) },
  }
}

/** A part's state as one line with its commands: Log, then Restart and Stop while it runs or Start
 * while it does not (a custom engine's page). */
export function PartStateLine({ part }: { part: EngineStatus }) {
  const commands = useCommands()
  const running = isRunning(part)
  return (
    <>
      <StatusLine
        tone={TONES[part.state]}
        action={[
          { label: 'Log', onClick: () => commands.showLog(part) },
          ...(running
            ? [
                { label: 'Restart', onClick: () => void commands.restart(part) },
                { label: 'Stop', onClick: () => void commands.stop(part) },
              ]
            : [{ label: 'Start', onClick: () => commands.start(part) }]),
        ]}
      >
        {STATE_WORDS[part.state]}
        {stateTime(part)}
        {part.state !== 'healthy' && part.detail ? `: ${part.detail}` : null}
        {waits(part)}
      </StatusLine>
      <EngineLog {...commands.log} />
    </>
  )
}

/** A part's state with how long it has been so ("Running, up 10 min", one line beside its icon) and whether
 * it waits for a restart, and its reason under it when it is not running. */
function PartState({ part }: { part: Part }) {
  const e = part.process
  const state: Part['state'] = e ? { state: e.state, detail: e.detail } : part.state
  if (!state) return part.off ? <span className="text-muted-foreground">Off</span> : null
  return (
    <div className="flex flex-col gap-0.5">
      <span className="relative z-10 whitespace-nowrap">
        <StateIcon state={state.state} detail={state.detail} word={(state.word ?? STATE_WORDS[state.state]) + (e ? stateTime(e) + waits(e) : '')} words="always" />
      </span>
      {state.state !== 'healthy' && state.detail ? <span className="text-sm text-muted-foreground">{state.detail}</span> : null}
    </div>
  )
}

/** The Settings landing: what needs you, what to set up next, his parts by role with their state and
 * commands, the graphics card, the custom engines once there is one, and who can open this page. */
export default function StatusPage() {
  const s = useSettings()
  const status = useStatus()
  const saved = useConfig().data
  const presets = $api.useQuery('get', '/api/presets')
  const extensions = $api.useQuery('get', '/api/extensions', undefined, { refetchInterval: 15_000 })
  const rooms = $api.useQuery('get', '/api/rooms')
  const setup = $api.useQuery('get', '/api/setup')
  const ha = saved?.providers?.home_assistant
  const haSetUp = !!ha?.url
  const haOn = haSetUp && ha?.enabled !== false
  // The same query as the Home Assistant page's: whether it answers, and its version.
  const exposure = $api.useQuery('get', '/api/providers/home_assistant/exposure', undefined, { enabled: haOn, retry: false })
  const actions = useEngineActions()
  const commands = useCommands()
  const ask = useConfirm()
  const navigate = useNavigate()
  const drivers = useDrivers()
  const [adding, setAdding] = useState<Adding | null>(null)
  if (s.loading || !status.data || !saved) return <PageSkeleton title="Status" />
  const { page } = s
  const { engines: processes, gpu, assistant: name } = status.data
  const proc = (n: string) => processes.find((e) => e.name === n)

  // His parts, from the saved config: what runs now.
  const preset = (kind: Kind) => {
    const list = presets.data?.[kind] ?? []
    const id = matchPreset(kind, saved, list)
    return list.find((p) => p.id === id)
  }
  const driverIcon = (driver?: string) => drivers.find((d) => d.value === driver)?.icon
  const slot = (kind: Kind, to: string, detail?: string | null): Part => {
    const engine = saved.engines?.[kind]
    const p = preset(kind)
    const inside = kind === 'stt' && !engine && !p?.title ? `Runs inside ${name}` : null
    const runs = [p?.title, detail, inside].filter(Boolean).join(', ')
    return {
      key: kind,
      icon: p?.icon ?? driverIcon(engine?.driver),
      role: SLOT_TITLES[kind],
      runs,
      to,
      process: proc(kind),
      off: engine?.enabled === false,
      enable: ['engines', kind, 'enabled'],
    }
  }
  // The model by its own name, without its maker, file format or quantization ("Qwen3.8-27B" for
  // unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S); the Brain page shows it whole.
  const model = saved.llm.model.split('/').pop()?.replace(/-GGUF(:.*)?$/i, '')
  const voiceDefault = section(page.schema, ['tts']).properties?.voice?.default
  const voice = saved.tts?.voice && saved.tts.voice !== voiceDefault ? saved.tts.voice.split('/').pop()?.replace(/\.[^.]+$/, '') : null
  const boxes = Object.keys(saved.channels?.satellite?.devices ?? {}).length
  const connected = rooms.data?.filter((r) => r.box && r.connected).length ?? 0
  const haDetail = exposure.isError ? errorText(exposure.error) : exposure.data?.ok === false ? exposure.data.detail : undefined
  const haDown = haOn && (exposure.isError || exposure.data?.ok === false)
  const full = !!gpu && gpu.used_mb / gpu.total_mb > 0.9
  const on = (extensions.data ?? []).filter((x) => x.enabled)
  const needing = on.filter(needsYou)

  const parts: Part[] = [
    slot('llm', '/brain', model),
    slot('tts', '/voice', voice),
    slot('stt', '/voice'),
    {
      key: 'voice',
      icon: 'voice',
      role: partName('voice'),
      runs: `In this browser, through ${name}`,
      to: '/ways-to-talk',
      process: proc('voice'),
      off: saved.channels?.voice?.enabled === false,
      enable: ['channels', 'voice', 'enabled'],
    },
    {
      key: 'satellite',
      icon: 'satellite',
      role: partName('satellite'),
      runs: boxes ? `${boxes} ${boxes === 1 ? 'box' : 'boxes'}, ${connected} connected` : 'No boxes yet',
      to: '/ways-to-talk',
      process: proc('satellite'),
      off: !saved.channels?.satellite?.enabled,
      // With no box, Set up next's Add a box covers it.
      enable: boxes ? ['channels', 'satellite', 'enabled'] : undefined,
    },
    {
      key: 'discord',
      icon: 'discord',
      role: partName('discord'),
      runs: 'Direct messages',
      to: '/ways-to-talk',
      process: proc('discord'),
      off: !saved.channels?.discord?.enabled,
      setUp: !saved.channels?.discord?.enabled,
    },
    {
      key: 'home_assistant',
      icon: 'homeassistant',
      role: 'Home Assistant',
      runs: !haSetUp ? 'Not set up' : exposure.data?.version ? `Version ${exposure.data.version}` : '',
      to: '/home-assistant',
      state:
        haOn && (exposure.data || exposure.isError)
          ? { state: haDown ? 'error' : 'healthy', detail: haDetail, word: haDown ? "Can't reach it" : 'Connected' }
          : undefined,
      off: haSetUp && !haOn,
      enable: haSetUp ? ['providers', 'home_assistant', 'enabled'] : undefined,
    },
    {
      key: 'extensions',
      icon: 'extension',
      role: 'Extensions',
      runs: on.length ? `${on.length} on${needing.length ? `, ${needing.length} ${needing.length === 1 ? 'needs' : 'need'} you` : ''}` : 'None on',
      to: '/extensions',
    },
  ]

  // Needs you: what is wrong now, each with what to do about it. What waits for a restart is in the header
  // and in its part's state.
  const needs: ReactNode[] = [
    ...processes
      .filter((e) => e.state === 'error' || e.state === 'unhealthy')
      .map((e) => (
        <StatusLine
          key={e.name}
          tone={TONES[e.state]}
          action={[
            { label: 'Log', onClick: () => commands.showLog(e) },
            { label: 'Restart', onClick: () => void commands.restart(e) },
            // Its page, where its settings and How it runs are.
            { label: `Open ${partName(e.name)}`, to: parts.find((p) => p.key === e.name)?.to ?? `/status/engines/${e.name}` },
          ]}
        >
          {reason(`${capitalize(engineLabel(e))} ${e.state === 'error' ? 'failed' : 'stopped responding'}`, e.detail)}
        </StatusLine>
      )),
    ...needing.map((x) => (
      <StatusLine key={x.id} tone={x.needs?.length ? 'warning' : 'error'} action={{ label: `Open ${x.name}`, to: `/extensions/${x.id}` }}>
        {x.needs?.length
          ? `${x.name} needs ${x.needs.length === 1 ? 'a key' : `${x.needs.length} keys`}.`
          : x.state === 'stopped'
            ? `${x.name} is on but not running.`
            : reason(`${x.name} ${x.state === 'problem' ? "can't run" : 'stopped'}`, x.problem || x.detail)}
      </StatusLine>
    )),
    ...(haDown
      ? [
          <StatusLine key="home_assistant" tone="error" action={{ label: 'Open Home Assistant', to: '/home-assistant' }}>
            {reason("Can't reach Home Assistant", haDetail)}
          </StatusLine>,
        ]
      : []),
  ]
  // The setup wizard while it is not finished, then each step skipped in it, until it is done there.
  const skipped = setup.data?.active ? [] : (setup.data?.order ?? []).filter((id) => setup.data?.steps[id] === 'skipped')
  const next: ReactNode[] = [
    ...(setup.data?.active
      ? [
          <StatusLine key="setup" tone="muted" action={{ label: 'Continue setup', to: '/setup' }}>
            Setup is not finished.
          </StatusLine>,
        ]
      : []),
    ...skipped.map((id) => (
      <StatusLine key={`setup-${id}`} tone="muted" action={{ label: 'Set it up', to: `/setup/${id}` }}>
        {`${SETUP_STEPS[id]?.title ?? id}: skipped during setup.`}
      </StatusLine>
    )),
    ...(!haSetUp
      ? [
          <StatusLine key="home_assistant" tone="muted" action={{ label: 'Set up', to: '/home-assistant' }}>
            Connect Home Assistant so he can see and control your home.
          </StatusLine>,
        ]
      : []),
    ...(haOn && Object.keys(ha?.expose ?? {}).length === 0
      ? [
          <StatusLine key="devices" tone="muted" action={{ label: 'Choose devices', to: '/home-assistant' }}>
            Choose the devices he may see and control.
          </StatusLine>,
        ]
      : []),
    ...(!boxes
      ? [
          <StatusLine key="boxes" tone="muted" action={{ label: 'Add a box', to: '/ways-to-talk' }}>
            Add a room box to talk to him without a screen.
          </StatusLine>,
        ]
      : []),
  ]

  const stopAll = async () => {
    const lines = [
      `He stops hearing, answering and speaking everywhere until you start him again.${gpu ? " The graphics card's memory is freed." : ''}`,
      ...processes.filter(isRunning).map((e) => `${partName(e.name)}: starting again takes ${restartCost(e)}`),
    ]
    if (await ask({ title: 'Stop everything?', lines, choices: [{ label: 'Stop all', value: 'yes', destructive: true }] })) actions.stopAll.mutate({})
  }

  const partColumns: Column<Part>[] = [
    {
      key: 'part',
      title: '',
      className: 'whitespace-normal',
      render: (p) => (
        <span className="flex items-start gap-3">
          <KindIcon name={p.icon} className="mt-0.5 text-muted-foreground" />
          <span className="flex flex-col">
            <span className="font-medium">{p.role}</span>
            {p.runs ? <span className="text-muted-foreground">{p.runs}</span> : null}
          </span>
        </span>
      ),
    },
    { key: 'state', title: '', className: 'whitespace-normal', render: (p) => <PartState part={p} /> },
  ]
  const partActions = (p: Part) => {
    const e = p.process
    // Gone once the draft turns it on; the save bar takes it from there.
    const turnOn = !e && p.off && p.enable && getIn(page.value, p.enable) !== true ? p.enable : undefined
    if (!e && !p.setUp && !turnOn) return null
    return (
      <span className="inline-flex items-center gap-1">
        {e ? (
          <Button variant="outline" size="sm" onClick={() => (isRunning(e) ? void commands.restart(e) : commands.start(e))}>
            {isRunning(e) ? 'Restart' : 'Start'}
          </Button>
        ) : turnOn ? (
          <Button variant="outline" size="sm" onClick={() => page.update((c) => setIn(c, turnOn, true))}>
            Turn on
          </Button>
        ) : p.setUp ? (
          <Button variant="outline" size="sm" asChild>
            <Link to={p.to}>Set up</Link>
          </Button>
        ) : null}
        {e ? (
          <RowMenu
            name={p.role}
            items={[
              ...(isRunning(e) ? [{ label: 'Stop', onSelect: () => void commands.stop(e) }] : []),
              { label: 'Log', onSelect: () => commands.showLog(e) },
            ]}
          />
        ) : null}
      </span>
    )
  }

  // Custom engines: the draft's, since they are edited here.
  const engines = page.value.engines ?? {}
  const custom = Object.keys(engines).filter((n) => !SLOTS.includes(n))
  const setEngines = (next: Record<string, EngineConfig>) => page.update((c) => ({ ...c, engines: next }))
  const customColumns: Column<string>[] = [
    { key: 'name', title: 'Name', render: (n) => <span className="font-medium">{n}</span> },
    { key: 'driver', title: 'Runs as', render: (n) => <DriverLabel driver={engines[n].driver} drivers={drivers} /> },
    {
      key: 'state',
      title: 'State',
      className: 'relative z-10',
      render: (n) => {
        const e = proc(n)
        if (e) return <StateIcon state={e.state} detail={e.detail} />
        return engines[n].enabled === false ? <span className="text-muted-foreground">Off</span> : null
      },
    },
  ]
  const remove = async (n: string) => {
    const e = proc(n)
    const lines = e && isRunning(e) ? ['It stops when you save.'] : []
    if (!(await ask({ title: `Remove ${n}?`, lines, choices: [{ label: 'Remove', value: 'yes', destructive: true }] }))) return
    const rest = { ...engines }
    delete rest[n]
    setEngines(rest)
  }
  const newName = adding?.name.trim() ?? ''
  const nameError = SLOTS.includes(newName)
    ? `Used by ${SLOT_TITLES[newName as Kind]}`
    : newName && !ENGINE.test(newName)
      ? 'Use small letters, digits, - and _, starting with a letter.'
      : null
  const add = () => {
    if (!adding) return
    setEngines({ ...engines, [newName]: { driver: adding.driver, port: nextPort(engines) } })
    setAdding(null)
    void navigate(`/status/engines/${newName}`)
  }
  const host = page.value.control?.host?.trim() ?? '127.0.0.1'

  return (
    <SettingsPage
      title="Status"
      intro={`Everything ${name} runs and connects to, and whether it works.`}
      page={page}
      commands={[
        ...(processes.some((e) => !isRunning(e)) ? [{ label: 'Start all', icon: Play, onClick: () => actions.startAll.mutate({}) }] : []),
        ...(processes.some(isRunning) ? [{ label: 'Stop all', icon: Square, onClick: () => void stopAll() }] : []),
        { label: 'Add a custom engine', icon: Plus, onClick: () => setAdding({ name: '', driver: 'docker' }) },
      ]}
    >
      {needs.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Needs you</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">{needs}</CardContent>
        </Card>
      ) : null}
      {next.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Set up next</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">{next}</CardContent>
        </Card>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle>His parts</CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable columns={partColumns} rows={parts} rowKey={(p) => p.key} rowLink={(p) => p.to} actions={partActions} />
        </CardContent>
      </Card>
      {gpu ? (
        <Card>
          <CardHeader>
            <CardTitle>Graphics memory</CardTitle>
            <CardDescription>
              The brain and the voices share it. {full ? 'Nearly full: a bigger model or voice will not fit.' : 'One that does not fit will not start.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <div className="flex flex-wrap justify-between gap-x-4 text-sm">
              <span>
                {(gpu.used_mb / 1024).toFixed(1)} of {(gpu.total_mb / 1024).toFixed(1)} GB in use
                {processes.some(isRunning) ? '' : ' by other programs'}
              </span>
              <span className="text-muted-foreground">{gpu.name}</span>
            </div>
            <Progress
              value={(gpu.used_mb / gpu.total_mb) * 100}
              className={cn(full && '*:data-[slot=progress-indicator]:bg-warning')}
            />
          </CardContent>
        </Card>
      ) : null}
      {custom.length ? (
        // Kept in sight once there is one: a custom engine has no row in His parts.
        <Card>
          <CardHeader>
            <CardTitle>Custom engines</CardTitle>
            <CardDescription>Other servers {name} starts and keeps running; nothing uses one until a setting points at its address.</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable
              columns={customColumns}
              rows={custom}
              rowKey={(n) => n}
              rowLink={(n) => `/status/engines/${n}`}
              actions={(n) => <RowMenu name={n} items={[{ label: 'Remove', destructive: true, onSelect: () => void remove(n) }]} />}
            />
          </CardContent>
        </Card>
      ) : null}
      <SectionForm page={page} path={CONTROL} title="Web address">
        <div className="flex flex-col items-start gap-2">
          {!LOCAL.includes(host) ? (
            <>
              <StatusLine tone="warning">Anyone on your network can control {name}{haSetUp ? ' and your home' : ''}.</StatusLine>
              {/* api/secrets.py refuses key writes while the page is open beyond this computer. */}
              <p className="text-sm text-muted-foreground">While it is open to your network, keys cannot be pasted on these pages.</p>
            </>
          ) : null}
          <p className="text-sm text-muted-foreground">Takes effect when Winston restarts.</p>
          <Button variant="outline" size="sm" asChild>
            <a href="/docs" target="_blank" rel="noreferrer">
              API reference
            </a>
          </Button>
        </div>
      </SectionForm>
      <EngineLog {...commands.log} />
      <ItemDialog
        open={adding !== null}
        onOpenChange={(open) => !open && setAdding(null)}
        title="Add a custom engine"
        keyLabel="Name"
        keyValue={adding?.name ?? ''}
        onKeyChange={(n) => setAdding((a) => a && { ...a, name: n })}
        existing={custom}
        canSubmit={!!newName && !nameError}
        onSubmit={add}
      >
        {nameError ? <span className="text-sm text-destructive">{nameError}</span> : null}
        <div className="flex flex-col gap-2">
          <Label htmlFor="engine-driver">Runs as</Label>
          <Select value={adding?.driver ?? 'docker'} onValueChange={(driver) => setAdding((a) => a && { ...a, driver: driver as Driver })}>
            <SelectTrigger id="engine-driver">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {drivers.map((d) => (
                <SelectItem key={d.value} value={d.value}>
                  <KindIcon name={d.icon} />
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </ItemDialog>
    </SettingsPage>
  )
}
