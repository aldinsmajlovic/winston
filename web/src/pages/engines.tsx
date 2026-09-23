import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router'
import { $api, type EngineConfig, type EngineStatus } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { ItemDialog } from '@/components/item-dialog'
import { KindIcon } from '@/components/kind-icon'
import { PageSkeleton } from '@/components/page'
import { StateIcon } from '@/components/state-icon'
import { ListPage, type Column } from '@/components/templates/list-page'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useConfirm } from '@/hooks/use-confirm'
import { useSettings } from '@/hooks/use-settings'

type Driver = EngineConfig['driver']
type Row = EngineConfig & { name: string; status?: EngineStatus }
type Adding = { name: string; driver: Driver }

const drivers: { value: Driver; label: string; icon: string }[] = [
  { value: 'docker', label: 'Docker container', icon: 'docker' },
  { value: 'subprocess', label: 'Process', icon: 'process' },
  { value: 'external', label: 'External server', icon: 'external' },
]

export default function EnginesPage() {
  const s = useSettings([])
  const status = $api.useQuery('get', '/api/status')
  const ask = useConfirm()
  const navigate = useNavigate()
  const [adding, setAdding] = useState<Adding | null>(null)
  if (s.loading) return <PageSkeleton title="Engines" />
  const { page } = s
  const engines = page.value.engines ?? {}
  const setEngines = (next: Record<string, EngineConfig>) => page.update((c) => ({ ...c, engines: next }))
  const rows: Row[] = Object.entries(engines).map(([name, e]) => ({
    name,
    ...e,
    status: status.data?.engines.find((x) => x.name === name),
  }))

  const columns: Column<Row>[] = [
    {
      key: 'state',
      title: '',
      className: 'w-0',
      render: (r) => <StateIcon state={r.status?.state ?? 'stopped'} detail={r.status?.detail} />,
    },
    { key: 'name', title: 'Name', render: (r) => <span className="font-medium">{r.name}</span> },
    {
      key: 'driver',
      title: 'Driver',
      render: (r) => {
        const d = drivers.find((x) => x.value === r.driver)
        return (
          <span className="flex items-center gap-2">
            <KindIcon name={d?.icon} className="text-muted-foreground" />
            {d?.label ?? r.driver}
          </span>
        )
      },
    },
    { key: 'port', title: 'Port', render: (r) => r.port },
    {
      key: 'enabled',
      title: 'Enabled',
      render: (r) => (
        <span onClick={(e) => e.stopPropagation()}>
          <Switch
            aria-label="Enabled"
            checked={r.enabled ?? true}
            onCheckedChange={(enabled) => setEngines({ ...engines, [r.name]: { ...engines[r.name], enabled } })}
          />
        </span>
      ),
    },
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
    setEngines({ ...engines, [name]: { driver: adding.driver, port: 8000 } })
    setAdding(null)
    void navigate(`/engines/${name}`)
  }

  return (
    <ListPage
      title="Engines"
      page={page}
      addLabel="Add engine"
      onAdd={() => setAdding({ name: '', driver: 'docker' })}
      columns={columns}
      rows={rows}
      rowKey={(r) => r.name}
      onRowClick={(r) => navigate(`/engines/${r.name}`)}
      actions={(r) => (
        <>
          <IconButton label="Edit" onClick={() => navigate(`/engines/${r.name}`)}>
            <Pencil />
          </IconButton>
          <IconButton label="Remove" onClick={() => remove(r.name)}>
            <Trash2 />
          </IconButton>
        </>
      )}
    >
      <ItemDialog
        open={adding !== null}
        onOpenChange={(open) => !open && setAdding(null)}
        title="New engine"
        keyLabel="Name"
        keyValue={adding?.name ?? ''}
        onKeyChange={(name) => setAdding((a) => a && { ...a, name })}
        canSubmit={Boolean(adding?.name.trim()) && !(adding!.name.trim() in engines)}
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
    </ListPage>
  )
}
