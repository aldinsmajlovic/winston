import { useState } from 'react'
import { Play, Plus, RotateCw, Square, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router'
import { $api, type EngineConfig, type EngineStatus } from '@/api/client'
import { useEngineActions } from '@/api/status'
import { IconButton } from '@/components/icon-button'
import { ItemDialog } from '@/components/item-dialog'
import { KindIcon } from '@/components/kind-icon'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { ChangedIcon, DriverLabel, StateIcon } from '@/components/state-icon'
import { DataTable, type Column } from '@/components/templates/list-page'
import { SettingsPage } from '@/components/templates/settings-page'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useConfirm } from '@/hooks/use-confirm'
import { useDrivers } from '@/hooks/use-drivers'
import { useSettings } from '@/hooks/use-settings'
import { duration, since } from '@/lib/format'

const LOGGING = ['logging']
const CONTROL = ['control']

type Driver = EngineConfig['driver']
/** An engine from the config or a channel from the status, with what runs now. */
type Row = { name: string; kind: EngineStatus['kind']; driver: string; port?: number | null; status?: EngineStatus; engine?: EngineConfig }
type Adding = { name: string; driver: Driver }

const running = (e?: EngineStatus) => !!e && e.state !== 'stopped' && e.state !== 'error'
const cost = (e: EngineStatus) =>
  e.started_in ? `about ${duration(e.started_in)}` : `up to ${duration(e.startup_timeout)}`

/** The brain's usual port is 8000; a new engine takes the next port above every one in use. */
const nextPort = (engines: Record<string, EngineConfig>) =>
  Math.max(7999, ...Object.values(engines).map((e) => e.port ?? 0)) + 1

/** What runs (engines and channels, their state and actions), the GPU, and Winston's own logging and
 * control settings. An engine's row opens its settings; a channel's opens Channels. */
export default function SystemPage() {
  const s = useSettings([LOGGING, CONTROL])
  const status = $api.useQuery('get', '/api/status')
  const actions = useEngineActions()
  const ask = useConfirm()
  const navigate = useNavigate()
  const drivers = useDrivers()
  const [adding, setAdding] = useState<Adding | null>(null)
  if (s.loading || !status.data) return <PageSkeleton title="System" />
  const { page } = s
  const { engines: processes, gpu, pending_control: control = [] } = status.data
  const engines = page.value.engines ?? {}
  const setEngines = (next: Record<string, EngineConfig>) => page.update((c) => ({ ...c, engines: next }))
  const statusOf = (name: string) => processes.find((p) => p.name === name)
  const rows: Row[] = [
    ...Object.entries(engines).map(([name, e]) => ({ name, kind: 'engine' as const, driver: e.driver, port: e.port, status: statusOf(name), engine: e })),
    ...processes.filter((p) => p.kind === 'channel').map((p) => ({ name: p.name, kind: p.kind, driver: p.driver, port: p.port, status: p })),
  ]
  const byName = (name: string) => ({ params: { path: { name } } })
  const confirmed = async (title: string, lines: string[], label: string, destructive: boolean, run: () => void) => {
    if (await ask({ title, lines, choices: [{ label, value: 'yes', destructive }] })) run()
  }

  const columns: Column<Row>[] = [
    { key: 'state', title: '', className: 'w-0', render: (r) => <StateIcon state={r.status?.state ?? 'stopped'} detail={r.status?.detail} /> },
    { key: 'name', title: 'Name', render: (r) => <span className="font-medium">{r.name}</span> },
    { key: 'kind', title: 'Kind', render: (r) => <Badge variant="outline">{r.kind}</Badge> },
    { key: 'driver', title: 'Driver', render: (r) => <DriverLabel driver={r.driver} drivers={drivers} /> },
    { key: 'port', title: 'Port', render: (r) => r.port || null },
    { key: 'since', title: 'For', render: (r) => (r.status ? since(r.status.since) : null) },
    {
      key: 'enabled',
      title: 'Enabled',
      render: (r) =>
        r.engine ? (
          <span onClick={(e) => e.stopPropagation()}>
            <Switch
              aria-label="Enabled"
              checked={r.engine.enabled ?? true}
              onCheckedChange={(enabled) => setEngines({ ...engines, [r.name]: { ...engines[r.name], enabled } })}
            />
          </span>
        ) : null,
    },
    { key: 'changed', title: '', className: 'w-0', render: (r) => (r.status?.config_changed ? <ChangedIcon /> : null) },
  ]

  const remove = async (name: string) => {
    const choice = await ask({
      title: `Remove ${name}?`,
      lines: ['A running process keeps going until stopped.'],
      choices: [{ label: 'Remove', value: 'yes', destructive: true }],
    })
    if (!choice) return
    const next = { ...engines }
    delete next[name]
    setEngines(next)
  }
  const add = () => {
    if (!adding) return
    const name = adding.name.trim()
    setEngines({ ...engines, [name]: { driver: adding.driver, port: nextPort(engines) } })
    setAdding(null)
    void navigate(`/system/engines/${name}`)
  }

  return (
    <SettingsPage
      title="System"
      page={page}
      actions={
        <>
          {control.length ? <ChangedIcon text={`Applies on the next winston up: ${control.join(', ')}`} /> : null}
          <IconButton label="Start all" variant="outline" size="icon" onClick={() => actions.startAll.mutate({})}>
            <Play />
          </IconButton>
          <IconButton
            label="Stop all"
            variant="outline"
            size="icon"
            onClick={() =>
              confirmed(
                'Stop everything?',
                processes.filter(running).map((e) => `${e.name}: starting again takes ${cost(e)}`),
                'Stop all',
                true,
                () => actions.stopAll.mutate({}),
              )
            }
          >
            <Square />
          </IconButton>
          <IconButton label="Add engine" variant="outline" size="icon" onClick={() => setAdding({ name: '', driver: 'docker' })}>
            <Plus />
          </IconButton>
        </>
      }
    >
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.name}
        onRowClick={(r) => navigate(r.engine ? `/system/engines/${r.name}` : '/channels')}
        actions={(r) => (
          <>
            {running(r.status) ? (
              <>
                <IconButton
                  label="Restart"
                  onClick={() =>
                    confirmed(`Restart ${r.name}?`, [cost(r.status!)], 'Restart', false, () => actions.restart.mutate(byName(r.name)))
                  }
                >
                  <RotateCw />
                </IconButton>
                <IconButton
                  label="Stop"
                  onClick={() =>
                    confirmed(`Stop ${r.name}?`, [`starting again takes ${cost(r.status!)}`], 'Stop', true, () =>
                      actions.stop.mutate(byName(r.name)),
                    )
                  }
                >
                  <Square />
                </IconButton>
              </>
            ) : r.status ? (
              <IconButton label="Start" onClick={() => actions.start.mutate(byName(r.name))}>
                <Play />
              </IconButton>
            ) : null}
            {r.engine ? (
              <IconButton label="Remove" onClick={() => remove(r.name)}>
                <Trash2 />
              </IconButton>
            ) : null}
          </>
        )}
      />
      {gpu ? (
        <Card>
          <CardHeader>
            <CardTitle>GPU</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Progress value={(gpu.used_mb / gpu.total_mb) * 100} />
            <div className="flex justify-between text-sm text-muted-foreground">
              <span>{gpu.name}</span>
              <span>
                {gpu.used_mb} / {gpu.total_mb} MB
              </span>
            </div>
          </CardContent>
        </Card>
      ) : null}
      <SectionForm page={page} path={LOGGING} title="Logging" />
      {page.advanced ? <SectionForm page={page} path={CONTROL} title="Control" /> : null}
      <ItemDialog
        open={adding !== null}
        onOpenChange={(open) => !open && setAdding(null)}
        title="New engine"
        keyLabel="Name"
        keyValue={adding?.name ?? ''}
        onKeyChange={(name) => setAdding((a) => a && { ...a, name })}
        existing={Object.keys(engines)}
        canSubmit={Boolean(adding?.name.trim())}
        onSubmit={add}
      >
        <div className="flex flex-col gap-2">
          <Label htmlFor="engine-driver">Driver</Label>
          <Select value={adding?.driver} onValueChange={(driver) => setAdding((a) => a && { ...a, driver: driver as Driver })}>
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
