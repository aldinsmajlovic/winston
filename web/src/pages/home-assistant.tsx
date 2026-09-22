import { useMemo, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import type { ExposedEntity, HomeAssistantConfig } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { ItemDialog } from '@/components/item-dialog'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { DataTable, type Column } from '@/components/templates/list-page'
import { SettingsPage } from '@/components/templates/settings-page'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useConfirm } from '@/hooks/use-confirm'
import { useSettings } from '@/hooks/use-settings'
import { prune, section } from '@/lib/schema'

const HA = ['providers', 'home_assistant']
const OMIT = ['expose']

type Row = ExposedEntity & { id: string }
type Editing = { id: string; original?: string; value: ExposedEntity }

const columns: Column<Row>[] = [
  { key: 'id', title: 'Entity', className: 'break-all whitespace-normal', render: (r) => <span className="font-mono text-xs">{r.id}</span> },
  { key: 'name', title: 'Name', className: 'whitespace-normal', render: (r) => r.name },
  { key: 'aliases', title: 'Aliases', className: 'whitespace-normal', render: (r) => r.aliases?.join(', ') },
]

export default function HomeAssistantPage() {
  const s = useSettings([HA])
  const ask = useConfirm()
  const [editing, setEditing] = useState<Editing | null>(null)
  const entitySchema = useMemo(
    () => (s.page ? prune(section(s.page.schema, [...HA, 'expose', 'item']), s.page.schema, { advanced: true }) : null),
    [s.page],
  )
  if (s.loading || !entitySchema) return <PageSkeleton title="Home Assistant" />
  const { page } = s
  const ha = page.value.providers?.home_assistant ?? null
  const setHa = (next: HomeAssistantConfig | null) =>
    page.update((c) => ({ ...c, providers: { ...c.providers, home_assistant: next } }))
  const rows: Row[] = Object.entries(ha?.expose ?? {}).map(([id, e]) => ({ id, ...e }))

  const commit = () => {
    if (!editing || !ha) return
    const expose = { ...ha.expose }
    if (editing.original && editing.original !== editing.id) delete expose[editing.original]
    expose[editing.id.trim()] = editing.value
    setHa({ ...ha, expose })
    setEditing(null)
  }
  const remove = async (id: string) => {
    const choice = await ask({ title: `Remove ${id}?`, choices: [{ label: 'Remove', value: 'yes', destructive: true }] })
    if (!choice || !ha) return
    const expose = { ...ha.expose }
    delete expose[id]
    setHa({ ...ha, expose })
  }

  return (
    <SettingsPage
      title="Home Assistant"
      page={page}
      actions={
        <div className="flex items-center gap-2">
          <Switch id="ha-enabled" checked={ha !== null} onCheckedChange={(on) => setHa(on ? { url: '' } : null)} />
          <Label htmlFor="ha-enabled">Enabled</Label>
        </div>
      }
    >
      {ha ? (
        <>
          <SectionForm page={page} path={HA} omit={OMIT} />
          <Card>
            <CardHeader>
              <CardTitle>Entities</CardTitle>
              <CardAction>
                <IconButton
                  label="Add entity"
                  variant="outline"
                  size="icon"
                  onClick={() => setEditing({ id: '', value: { aliases: [] } })}
                >
                  <Plus />
                </IconButton>
              </CardAction>
            </CardHeader>
            <CardContent>
              <DataTable
                columns={columns}
                rows={rows}
                rowKey={(r) => r.id}
                onRowClick={(r) => setEditing({ id: r.id, original: r.id, value: { name: r.name, aliases: r.aliases } })}
                actions={(r) => (
                  <IconButton label="Remove" onClick={() => remove(r.id)}>
                    <Trash2 />
                  </IconButton>
                )}
              />
            </CardContent>
          </Card>
          <ItemDialog
            open={editing !== null}
            onOpenChange={(open) => !open && setEditing(null)}
            title={editing?.original ? editing.original : 'New entity'}
            keyLabel="Entity id"
            keyValue={editing?.id ?? ''}
            onKeyChange={(id) => setEditing((e) => e && { ...e, id })}
            schema={entitySchema}
            value={editing?.value}
            onChange={(value) => setEditing((e) => e && { ...e, value: value as ExposedEntity })}
            onSubmit={commit}
          />
        </>
      ) : null}
    </SettingsPage>
  )
}
