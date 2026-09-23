import { useMemo, useState } from 'react'
import { CircleCheck, CircleX, LoaderCircle, Plus, Trash2 } from 'lucide-react'
import { $api, type ExposedEntity, type HomeAssistantConfig } from '@/api/client'
import { Combobox, type Choice } from '@/components/combobox'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { ItemDialog } from '@/components/item-dialog'
import { KindIcon } from '@/components/kind-icon'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { DataTable, type Column } from '@/components/templates/list-page'
import { SettingsPage } from '@/components/templates/settings-page'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfirm } from '@/hooks/use-confirm'
import { useOptions } from '@/hooks/use-options'
import { useSettings } from '@/hooks/use-settings'
import { prune, section } from '@/lib/schema'

const HA = ['providers', 'home_assistant']
const OMIT = ['expose']

type Row = ExposedEntity & { id: string; ha?: Choice }
type Editing = { id: string; original?: string; value: ExposedEntity }

const domain = (id: string) => id.split('.')[0]

const columns: Column<Row>[] = [
  { key: 'icon', title: '', className: 'w-0', render: (r) => <KindIcon name={domain(r.id)} className="text-muted-foreground" /> },
  {
    key: 'name',
    title: 'Entity',
    className: 'whitespace-normal',
    render: (r) => (
      <div className="flex flex-col">
        <span className={r.name ? 'font-medium' : undefined}>{r.name ?? r.ha?.label ?? r.id}</span>
        <span className="text-xs break-all text-muted-foreground">{r.id}</span>
      </div>
    ),
  },
  { key: 'aliases', title: 'Aliases', className: 'whitespace-normal', render: (r) => r.aliases?.join(', ') },
  { key: 'state', title: 'State', className: 'text-muted-foreground', render: (r) => r.ha?.hint },
]

/** Whether Winston reaches Home Assistant at the draft's URL with its token: an icon, the detail on hover. */
function Connection({ url }: { url: string }) {
  const check = $api.useQuery('get', '/api/providers/home_assistant/check', { params: { query: { url } } }, { enabled: url !== '' })
  if (!url) return null
  const data = check.data
  const icon = !data ? (
    <LoaderCircle className="size-4 animate-spin text-muted-foreground" aria-label="checking" />
  ) : data.ok ? (
    <CircleCheck className="size-4 text-success" aria-label="connected" />
  ) : (
    <CircleX className="size-4 text-destructive" aria-label="not connected" />
  )
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{icon}</span>
      </TooltipTrigger>
      <TooltipContent>{data?.detail ?? 'Checking'}</TooltipContent>
    </Tooltip>
  )
}

export default function HomeAssistantPage() {
  const s = useSettings([HA])
  const ask = useConfirm()
  const entities = useOptions('ha_entities')
  const [editing, setEditing] = useState<Editing | null>(null)
  const entitySchema = useMemo(
    () => (s.page ? prune(section(s.page.schema, [...HA, 'expose', 'item']), s.page.schema, { advanced: true }) : null),
    [s.page],
  )
  if (s.loading || !entitySchema) return <PageSkeleton title="Home Assistant" />
  const { page } = s
  const exposeHelp = section(page.schema, [...HA, 'expose']).description
  const ha = page.value.providers?.home_assistant ?? null
  const setHa = (next: HomeAssistantConfig | null) =>
    page.update((c) => ({ ...c, providers: { ...c.providers, home_assistant: next } }))
  const byId = new Map(entities.choices.map((c) => [c.value, c]))
  const rows: Row[] = Object.entries(ha?.expose ?? {}).map(([id, e]) => ({ id, ...e, ha: byId.get(id) }))
  const exposed = new Set(Object.keys(ha?.expose ?? {}))
  const pickable = entities.choices.filter((c) => !exposed.has(c.value) || c.value === editing?.original)

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
          {ha ? <Connection url={ha.url} /> : null}
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
              <CardTitle className="flex items-center gap-1.5">
                Entities
                <InfoTip text={exposeHelp} />
              </CardTitle>
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
            title={editing?.original ? (byId.get(editing.original)?.label ?? editing.original) : 'New entity'}
            keyLabel="Entity"
            keyValue={editing?.id ?? ''}
            keyInput={
              <Combobox
                id="item-key"
                value={editing?.id}
                choices={pickable}
                custom
                loading={entities.loading}
                problem={entities.problem}
                placeholder="Pick an entity"
                onChange={(id) => setEditing((e) => e && { ...e, id })}
              />
            }
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
