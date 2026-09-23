import { useMemo, useState } from 'react'
import { Trash2 } from 'lucide-react'
import type { SensorPair } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { ItemDialog } from '@/components/item-dialog'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { ListPage, type Column } from '@/components/templates/list-page'
import { useConfirm } from '@/hooks/use-confirm'
import { useSettings } from '@/hooks/use-settings'
import { prune, section } from '@/lib/schema'

const SENSORS = ['tools', 'sensors']
const WEB = ['tools', 'web_search']
const CLAUDE = ['tools', 'ask_claude']
const MEMORY = ['memory']

type Row = SensorPair & { label: string }
type Editing = { label: string; original?: string; value: SensorPair }

const columns: Column<Row>[] = [
  { key: 'label', title: 'Label', render: (r) => <span className="font-medium">{r.label}</span> },
  { key: 'level', title: 'Value sensor', className: 'break-all whitespace-normal', render: (r) => <span className="font-mono text-xs">{r.level}</span> },
  { key: 'state', title: 'State sensor', className: 'break-all whitespace-normal', render: (r) => <span className="font-mono text-xs">{r.state}</span> },
]

export default function ToolsPage() {
  const s = useSettings([WEB, CLAUDE, MEMORY])
  const ask = useConfirm()
  const [editing, setEditing] = useState<Editing | null>(null)
  const sensorSchema = useMemo(
    () => (s.page ? prune(section(s.page.schema, [...SENSORS, 'item']), s.page.schema, { advanced: true }) : null),
    [s.page],
  )
  if (s.loading || !sensorSchema) return <PageSkeleton title="Tools" />
  const { page } = s
  const sensors = page.value.tools?.sensors ?? {}
  const setSensors = (next: Record<string, SensorPair>) =>
    page.update((c) => ({ ...c, tools: { ...c.tools, sensors: next } }))
  const rows: Row[] = Object.entries(sensors).map(([label, pair]) => ({ label, ...pair }))

  const commit = () => {
    if (!editing) return
    const next = { ...sensors }
    if (editing.original && editing.original !== editing.label) delete next[editing.original]
    next[editing.label.trim()] = editing.value
    setSensors(next)
    setEditing(null)
  }
  const remove = async (label: string) => {
    const choice = await ask({ title: `Remove ${label}?`, choices: [{ label: 'Remove', value: 'yes', destructive: true }] })
    if (!choice) return
    const next = { ...sensors }
    delete next[label]
    setSensors(next)
  }

  return (
    <ListPage
      title="Tools"
      page={page}
      addLabel="Add sensor"
      onAdd={() => setEditing({ label: '', value: { level: '' } })}
      columns={columns}
      rows={rows}
      rowKey={(r) => r.label}
      onRowClick={(r) => setEditing({ label: r.label, original: r.label, value: { level: r.level, state: r.state } })}
      actions={(r) => (
        <IconButton label="Remove" onClick={() => remove(r.label)}>
          <Trash2 />
        </IconButton>
      )}
    >
      <SectionForm page={page} path={WEB} title="Web search" />
      <SectionForm page={page} path={CLAUDE} title="Ask Claude" />
      <SectionForm page={page} path={MEMORY} title="Memory" />
      <ItemDialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing?.original ? editing.original : 'New sensor'}
        keyLabel="Label"
        keyValue={editing?.label ?? ''}
        onKeyChange={(label) => setEditing((e) => e && { ...e, label })}
        schema={sensorSchema}
        value={editing?.value}
        onChange={(value) => setEditing((e) => e && { ...e, value: value as SensorPair })}
        onSubmit={commit}
      />
    </ListPage>
  )
}
