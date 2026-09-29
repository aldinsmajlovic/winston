import { useMemo, useState } from 'react'
import { CircleAlert, Plus } from 'lucide-react'
import { errorText, type ExposedEntity, type HomeAssistantConfig } from '@/api/client'
import { useConfig } from '@/api/config'
import { Combobox } from '@/components/combobox'
import { HAConnectionStatus } from '@/components/ha-connection'
import { HAEntityTables } from '@/components/ha-entities'
import { HAEntityPicker } from '@/components/ha-entity-picker'
import { InfoTip } from '@/components/info-tip'
import { ItemDialog } from '@/components/item-dialog'
import { PageSkeleton } from '@/components/page'
import { SearchField } from '@/components/search-field'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useConfirm } from '@/hooks/use-confirm'
import {
  groupByArea,
  matches,
  useHomeAssistant,
  type EntityRow,
  type HAEntity,
  type HAExposure,
  type HATool,
  type RowStatus,
} from '@/hooks/use-home-assistant'
import { useOptions } from '@/hooks/use-options'
import { useSettings } from '@/hooks/use-settings'
import { deepEqual, prune, section } from '@/lib/schema'

const HA = ['providers', 'home_assistant']
const OMIT = ['expose', 'enabled', 'verify']
const VERIFY = [...HA, 'verify']

type Editing = { id: string; original?: string; value: ExposedEntity }

const TOOLS_HELP =
  "What Home Assistant's MCP server lets him do. It follows what is exposed: a light brings brightness and colour, a to-do list its own tools. The voice channel reads them when it starts."

/** A listed entity as Home Assistant has it: applied, waiting for a save or for Home Assistant, or unknown. */
function rowStatus(id: string, entry: ExposedEntity, saved: ExposedEntity | undefined, exposure: HAExposure | undefined, ha?: HAEntity): RowStatus {
  if (!saved || !deepEqual({ name: saved.name ?? null, aliases: saved.aliases ?? [] }, { name: entry.name ?? null, aliases: entry.aliases ?? [] })) {
    return { kind: 'pending', why: ['Save to apply it to Home Assistant'] }
  }
  if (!exposure) return { kind: 'unknown', why: 'Home Assistant has not answered yet' }
  if (!exposure.ok) return { kind: 'unknown', why: exposure.detail || 'Home Assistant is not reachable' }
  const e = exposure.entities?.find((x) => x.entity_id === id) ?? ha
  if (!e) return { kind: 'unknown', why: 'Not in the last answer from Home Assistant' }
  if (!e.in_ha) return { kind: 'missing' }
  if (e.applied === true) return { kind: 'applied' }
  if (e.applied == null) return { kind: 'unknown', why: 'Checking needs an admin token' }
  const why: string[] = []
  if (e.exposed === false) why.push('Not exposed to Assist')
  if (e.missing_aliases?.length) why.push(`Missing aliases: ${e.missing_aliases.join(', ')}`)
  why.push('Sync, or Apply beside Connection')
  return { kind: 'pending', why }
}

function ToolsCard({ tools }: { tools: { data?: HATool[]; error: unknown; isLoading: boolean } }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          What he can do
          <InfoTip text={TOOLS_HELP} />
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Table aria-label="What he can do">
          <TableHeader>
            <TableRow>
              <TableHead>Tool</TableHead>
              <TableHead>From</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tools.error ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={2}>
                  <span className="flex items-center gap-2 whitespace-normal">
                    <CircleAlert className="size-4 shrink-0 text-warning" />
                    {errorText(tools.error)}
                  </span>
                </TableCell>
              </TableRow>
            ) : tools.data && tools.data.length === 0 ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={2} className="text-muted-foreground">
                  None yet.
                </TableCell>
              </TableRow>
            ) : null}
            {(tools.data ?? []).map((t) => (
              <TableRow key={t.names.join(' ')} title={t.names.join(', ')}>
                <TableCell>{t.label}</TableCell>
                <TableCell className="text-muted-foreground">{t.source}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

export default function HomeAssistantPage() {
  const s = useSettings([HA])
  const ask = useConfirm()
  const config = useConfig()
  const entities = useOptions('ha_entities')
  const [editing, setEditing] = useState<Editing | null>(null)
  // The picker gets a new key each time it opens, so it starts with nothing ticked.
  const [picker, setPicker] = useState({ open: false, key: 0 })
  const [search, setSearch] = useState('')
  const saved: HomeAssistantConfig | null = config.data?.providers?.home_assistant ?? null
  const savedOn = !!saved && saved.enabled !== false && !!saved.url
  const { exposure, registry, tools } = useHomeAssistant(savedOn)
  const entitySchema = useMemo(
    () => (s.page ? prune(section(s.page.schema, [...HA, 'expose', 'item']), s.page.schema, { advanced: true }) : null),
    [s.page],
  )
  if (s.loading || !entitySchema) return <PageSkeleton title="Home Assistant" />
  const { page } = s
  const exposeHelp = section(page.schema, [...HA, 'expose']).description
  const domainsHelp = section(page.schema, [...HA, 'expose_domains']).description
  const ha: HomeAssistantConfig | null = page.value.providers?.home_assistant ?? null
  const setHa = (next: HomeAssistantConfig | null) => page.update((c) => ({ ...c, providers: { ...c.providers, home_assistant: next } }))
  // Off keeps the URL, token and entities (enabled: false); a schema without the field unsets the block.
  const hasEnabled = 'enabled' in (section(page.schema, HA).properties ?? {})
  const on = ha !== null && ha.enabled !== false
  const toggle = (next: boolean) => {
    if (!hasEnabled) setHa(next ? { url: '' } : null)
    else if (ha) setHa({ ...ha, enabled: next })
    else if (next) setHa({ url: '', enabled: true })
  }

  // Home Assistant's side of each entity: the registry has every one, the exposure the listed ones.
  const known = new Map<string, HAEntity>()
  for (const e of exposure.data?.entities ?? []) known.set(e.entity_id, e)
  for (const g of registry.data?.groups ?? []) for (const e of g.entities) known.set(e.entity_id, e)
  const exposureData = savedOn ? exposure.data : undefined
  const rows: EntityRow[] = Object.entries(ha?.expose ?? {}).map(([id, entry]) => {
    const info = known.get(id)
    const status: RowStatus = savedOn
      ? rowStatus(id, entry, saved?.expose?.[id], exposureData, info)
      : { kind: 'pending', why: ['Save to apply it to Home Assistant'] }
    return { id, entry, ha: info, status }
  })
  const areasKnown = registry.data?.areas_known ?? exposure.data?.registry ?? false
  const groups = groupByArea(
    rows.filter((r) => matches(r, search)),
    areasKnown,
  )
  const listed = new Set(Object.keys(ha?.expose ?? {}))
  const domains = ha?.expose_domains ?? []
  const domainCounts = exposure.data?.domains ?? {}
  const pickable = entities.choices.filter((c) => !listed.has(c.value) || c.value === editing?.original)

  const setExpose = (expose: Record<string, ExposedEntity>) => ha && setHa({ ...ha, expose })
  const commit = () => {
    if (!editing || !ha) return
    const expose = { ...ha.expose }
    if (editing.original && editing.original !== editing.id) delete expose[editing.original]
    expose[editing.id.trim()] = editing.value
    setExpose(expose)
    setEditing(null)
  }
  const remove = async (row: EntityRow) => {
    const choice = await ask({
      title: `Remove ${row.entry.name || row.ha?.friendly_name || row.id}?`,
      lines: ['Save un-exposes it and removes the aliases the list gave it in Home Assistant'],
      choices: [{ label: 'Remove', value: 'yes', destructive: true }],
    })
    if (!choice || !ha) return
    const expose = { ...ha.expose }
    delete expose[row.id]
    setExpose(expose)
  }
  /** An added entry starts with Home Assistant's name and no aliases of its own. Home Assistant's aliases
   * already work and show on the row; copying them into the list would make them the list's, and taking
   * the entity off the list later would then remove aliases a person added in Home Assistant. */
  const fromHa = (e: HAEntity | undefined): ExposedEntity => ({
    ...(e?.friendly_name ? { name: e.friendly_name } : {}),
    aliases: [],
  })
  const addPicked = (picked: HAEntity[]) => {
    if (!ha) return
    const expose = { ...ha.expose }
    for (const e of picked) expose[e.entity_id] = fromHa(e)
    setExpose(expose)
  }
  /** Picking an id while adding fills the name and aliases from Home Assistant, unless typed already. */
  const pickId = (id: string) =>
    setEditing((e) => {
      if (!e) return e
      const blank = !e.original && !e.value.name && !e.value.aliases?.length
      return { ...e, id, value: blank ? fromHa(known.get(id)) : e.value }
    })
  const draftUrl = ha && saved && ha.url !== saved.url ? ha.url : undefined

  return (
    <SettingsPage
      title="Home Assistant"
      page={page}
      actions={
        <div className="flex items-center gap-2">
          <Switch id="ha-enabled" checked={on} onCheckedChange={toggle} />
          <Label htmlFor="ha-enabled" className="max-sm:sr-only">
            Enabled
          </Label>
        </div>
      }
    >
      {ha && on ? (
        <>
          <SectionForm
            page={page}
            path={HA}
            omit={OMIT}
            title="Connection"
            status={
              savedOn || draftUrl !== undefined ? (
                <HAConnectionStatus exposure={exposure} draftUrl={savedOn ? draftUrl : ha.url} dirty={page.dirty} />
              ) : null
            }
          />
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-1.5">
                What he sees
                <InfoTip text={exposeHelp} />
              </CardTitle>
              <CardAction>
                <Button variant="outline" size="sm" onClick={() => setPicker((p) => ({ open: true, key: p.key + 1 }))}>
                  <Plus />
                  Add from Home Assistant
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {rows.length ? (
                <SearchField value={search} onChange={setSearch} placeholder="Search names, aliases or rooms" />
              ) : null}
              <HAEntityTables
                groups={groups}
                empty={rows.length ? 'Nothing matches.' : 'None yet.'}
                onEdit={(r) => setEditing({ id: r.id, original: r.id, value: { ...r.entry } })}
                onRemove={(r) => void remove(r)}
              />
              {domains.length ? (
                <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                  <span>Also everything in: {domains.join(', ')}</span>
                  <InfoTip
                    text={[
                      domainsHelp,
                      ...domains.map((d) => (domainCounts[d] ? `${d}: ${domainCounts[d].length} in Home Assistant` : null)),
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  />
                </div>
              ) : null}
            </CardContent>
          </Card>
          {savedOn ? <ToolsCard tools={tools} /> : null}
          {'verify' in (section(page.schema, HA).properties ?? {}) ? (
            <SectionForm page={page} path={VERIFY} title="After an action" />
          ) : null}
          <HAEntityPicker
            key={picker.key}
            open={picker.open}
            onOpenChange={(open) => setPicker((p) => ({ ...p, open }))}
            registry={registry}
            listed={listed}
            onAdd={addPicked}
            onTypeId={() => {
              setPicker((p) => ({ ...p, open: false }))
              setEditing({ id: '', value: { aliases: [] } })
            }}
          />
          <ItemDialog
            open={editing !== null}
            onOpenChange={(open) => !open && setEditing(null)}
            title={editing?.original ? (rows.find((r) => r.id === editing.original)?.entry.name ?? known.get(editing.original)?.friendly_name ?? editing.original) : 'New entity'}
            keyLabel="Entity"
            keyValue={editing?.id ?? ''}
            existing={listed}
            original={editing?.original}
            keyInput={
              <Combobox
                id="item-key"
                value={editing?.id}
                choices={pickable}
                custom
                loading={entities.loading}
                problem={entities.problem}
                placeholder="Pick an entity"
                onChange={pickId}
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
