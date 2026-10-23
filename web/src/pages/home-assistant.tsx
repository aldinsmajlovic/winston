import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { errorText, type ExposedEntity, type HomeAssistantConfig } from '@/api/client'
import { useConfig } from '@/api/config'
import { ActionList } from '@/components/action-list'
import { Combobox } from '@/components/combobox'
import { HACheck, HAConnectionStatus, HADiscovered, HASignInButton, HASignInResult, type SignInBack } from '@/components/ha-connection'
import { HAEntityTables } from '@/components/ha-entities'
import { HAEntityPicker } from '@/components/ha-entity-picker'
import { ItemDialog } from '@/components/item-dialog'
import { KindIcon } from '@/components/kind-icon'
import { PageSkeleton } from '@/components/page'
import { SchemaForm } from '@/components/schema-form'
import { SearchField } from '@/components/search-field'
import { SectionForm } from '@/components/section-form'
import { StatusLine } from '@/components/status-line'
import { SettingsPage } from '@/components/templates/settings-page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useConfirm } from '@/hooks/use-confirm'
import {
  groupByArea,
  haActionItems,
  haNames,
  heardName,
  kindName,
  matches,
  otherNames,
  planParts,
  useApplyNow,
  useHomeAssistant,
  type EntityRow,
  type HAEntity,
  type HAExposure,
} from '@/hooks/use-home-assistant'
import { useOptions } from '@/hooks/use-options'
import { useSecretStatus, useSecretsDraft } from '@/hooks/use-secrets'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { joinNames } from '@/lib/format'
import { allBut, deepEqual, prune, section } from '@/lib/schema'

const HA = ['providers', 'home_assistant']
const VERIFY = [...HA, 'verify']
// What other cards on the page edit, left out of the Connection card.
const LISTS = ['expose', 'expose_domains', 'verify']

type Editing = { id: string; original?: string; value: ExposedEntity }

/** What is not right yet with a listed entity, in words; null when Home Assistant has it as listed, or
 * when that cannot be known (the Connection card says why). */
function rowBadge(entry: ExposedEntity, saved: ExposedEntity | undefined, exposure: HAExposure | undefined, e?: HAEntity): string | null {
  const names = (x: ExposedEntity) => ({ name: x.name ?? null, aliases: x.aliases ?? [] })
  if (!saved || !deepEqual(names(saved), names(entry))) return 'Waiting for Save'
  if (!exposure?.ok || !e) return null
  if (!e.in_ha) return 'Not in Home Assistant'
  if (e.applied !== false) return null
  return e.exposed === false ? 'Not exposed yet' : 'Names not added yet'
}

const INTRO = 'Connect Home Assistant and choose what he sees and controls there; saving also sets what its own assistant, Assist, exposes.'

export default function HomeAssistantPage() {
  const s = useSettings()
  if (s.loading) return <PageSkeleton title="Home Assistant" />
  return (
    <SettingsPage title="Home Assistant" intro={INTRO} page={s.page}>
      <HomeAssistantSections page={s.page} back="/home-assistant" />
    </SettingsPage>
  )
}

/** The page's cards, which the setup wizard's Home Assistant step draws too (`back`, where signing in comes
 * back to), there without What he can do there. Not set up (no address, or no token saved for it), it is
 * Connect Home Assistant: those found on the network, the address with Sign in, and a token to paste. */
export function HomeAssistantSections({ page, back }: { page: SettingsState; back: SignInBack }) {
  const ask = useConfirm()
  const client = useQueryClient()
  const config = useConfig()
  const secrets = useSecretsDraft()
  const apply = useApplyNow()
  const entities = useOptions('ha_entities')
  const [editing, setEditing] = useState<Editing | null>(null)
  // The picker gets a new key each time it opens, so it starts with nothing ticked.
  const [picker, setPicker] = useState({ open: false, key: 0 })
  const [sharing, setSharing] = useState(false)
  const [search, setSearch] = useState('')
  const keys = useSecretStatus()
  const saved: HomeAssistantConfig | null = config.data?.providers?.home_assistant ?? null
  // Set up once an address is saved with its token (a new install has the example's address and none);
  // until then the page is the Connect card. While the keys load, an address counts.
  const setUp = !!saved?.url && keys.byName.get(saved.token_env || 'HA_TOKEN')?.set !== false
  const savedOn = setUp && saved.enabled !== false
  const { exposure, registry, tools } = useHomeAssistant(savedOn)
  // Say first, Stay quiet and Run at its name are for scripts and scenes (Run at its name: scripts only).
  const editId = editing?.id ?? ''
  const entitySchema = useMemo(() => {
    const runs = editId.startsWith('script.') || editId.startsWith('scene.')
    const omit = [...(runs ? [] : ['say_first', 'quiet_after']), ...(editId.startsWith('script.') ? [] : ['shortcut'])]
    return prune(section(page.schema, [...HA, 'expose', 'item']), page.schema, { omit })
  }, [page.schema, editId])
  const inSetup = back === '/setup/home_assistant'
  const ha: HomeAssistantConfig | null = page.value.providers?.home_assistant ?? null
  const setHa = (next: HomeAssistantConfig | null) => page.update((c) => ({ ...c, providers: { ...c.providers, home_assistant: next } }))
  const on = ha !== null && ha.enabled !== false
  const token = secrets.pending[ha?.token_env || 'HA_TOKEN']

  if (!setUp) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KindIcon name="homeassistant" />
            Connect Home Assistant
          </CardTitle>
          <CardDescription>Home Assistant gives him your devices, rooms and sensors, and lets your automations make him speak.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <HASignInResult />
          <HADiscovered back={back} />
          {/* The address with Sign in under it, then the token to paste instead. */}
          <SectionForm page={page} path={HA} omit={allBut(page.schema, HA, ['url'])} />
          <HASignInButton url={ha?.url} back={back} />
          <SectionForm page={page} path={HA} omit={allBut(page.schema, HA, ['token_env'])} />
          {token ? <HACheck url={ha?.url} token={token} /> : null}
          <p className="text-sm text-muted-foreground">
            {token
              ? 'After you save, choose which devices he can use. Nothing changes in Home Assistant until you do.'
              : 'Signed in or with a pasted token, you choose next which devices he can use. Nothing changes in Home Assistant until you do.'}
          </p>
        </CardContent>
      </Card>
    )
  }

  // Home Assistant's side of each entity: the registry has every one, the exposure the listed ones.
  const known = new Map<string, HAEntity>()
  for (const e of exposure.data?.entities ?? []) known.set(e.entity_id, e)
  for (const g of registry.data?.groups ?? []) for (const e of g.entities) if (!known.has(e.entity_id)) known.set(e.entity_id, e)
  const exposureData = savedOn ? exposure.data : undefined
  const rows: EntityRow[] = Object.entries(ha?.expose ?? {}).map(([id, entry]) => {
    const info = known.get(id)
    const badge = savedOn ? rowBadge(entry, saved.expose?.[id], exposureData, info) : 'Waiting for Save'
    return { id, entry, ha: info, badge }
  })
  const areasKnown = registry.data?.areas_known ?? exposure.data?.registry ?? false
  const groups = groupByArea(
    rows.filter((r) => matches(r, search)),
    areasKnown,
  )
  const listed = new Set(Object.keys(ha?.expose ?? {}))
  const domains = ha?.expose_domains ?? []
  const domainCounts = exposureData?.domains ?? {}
  const pickable = entities.choices.filter((c) => !listed.has(c.value) || c.value === editing?.original)

  // Exceptions only: what Home Assistant exposes beyond the list, what an apply would still do while the
  // list itself is saved, and listed ids Home Assistant does not have.
  const drift = exposureData?.ok ? (exposureData.drift ?? []).filter((e) => !listed.has(e.entity_id)) : []
  const one = drift.length === 1
  const lists = (h: HomeAssistantConfig | null) => ({ expose: h?.expose ?? {}, domains: h?.expose_domains ?? null })
  const plan =
    exposureData?.ok && exposureData.registry && !exposureData.in_sync && deepEqual(lists(ha), lists(saved)) ? exposureData.pending : null
  // Only what the list asks for: un-exposing the drift alone is the line above's.
  const driftIds = new Set(drift.map((e) => e.entity_id))
  const pending =
    plan &&
    (!!plan.expose?.length ||
      Object.keys(plan.aliases_add ?? {}).length > 0 ||
      Object.keys(plan.aliases_remove ?? {}).length > 0 ||
      (plan.unexpose ?? []).some((id) => !driftIds.has(id)))
      ? planParts(plan)
      : []
  const applyNow = async () => {
    if (!plan) return
    const choice = await ask({
      title: 'Apply the list in Home Assistant now?',
      lines: [`Home Assistant ${joinNames(planParts(plan, haNames(client)))}.`],
      choices: [{ label: 'Apply now', value: 'yes', destructive: (plan.unexpose?.length ?? 0) > 0 }],
    })
    if (choice === 'yes') apply.mutate({})
  }
  const missing = rows.filter((r) => r.badge === 'Not in Home Assistant').map(heardName)

  const setExpose = (expose: Record<string, ExposedEntity>) => ha && setHa({ ...ha, expose })
  const commit = () => {
    if (!editing || !ha) return
    const expose = { ...ha.expose }
    if (editing.original && editing.original !== editing.id) delete expose[editing.original]
    // rjsf's Add leaves a blank row until it is typed in; the config refuses it at Save.
    expose[editing.id.trim()] = { ...editing.value, aliases: (editing.value.aliases ?? []).filter((a) => typeof a === 'string' && a.trim() !== '') }
    setExpose(expose)
    setEditing(null)
  }
  const remove = async (row: EntityRow) => {
    const choice = await ask({
      title: `Remove ${heardName(row)}?`,
      lines: ['When you save, Home Assistant stops exposing it and drops the names this list gave it.'],
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
  const editingRow = editing?.original ? rows.find((r) => r.id === editing.original) : undefined
  // Home Assistant's own names for the entity in the dialog: its name and aliases, less the ones this list
  // gives it (saved or not), which the list edits.
  const theirHa = editing ? known.get(editing.id) : undefined
  const savedEntry = editing?.original ? saved.expose?.[editing.original] : undefined
  const theirs =
    editing && theirHa
      ? otherNames({
          id: editing.id,
          entry: { ...editing.value, aliases: [...(editing.value.aliases ?? []), savedEntry?.name ?? '', ...(savedEntry?.aliases ?? [])] },
          ha: { ...theirHa, aliases: [theirHa.friendly_name ?? '', ...theirHa.aliases] },
          badge: null,
        }).ha
      : []

  // The API declares no error for it, so the query types it never; a 502 says why Home Assistant did not answer.
  const toolsError: unknown = tools.error
  const actions = haActionItems(tools.data ?? [])

  return (
    <>
      <SectionForm page={page} path={HA} omit={LISTS} title="Connection">
        <HASignInResult />
        {on ? (
          ha.url !== saved.url || token ? (
            <HACheck url={ha.url} token={token} />
          ) : savedOn ? (
            <HAConnectionStatus exposure={exposure} />
          ) : null
        ) : null}
      </SectionForm>
      {on ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Devices he can use</CardTitle>
              <CardDescription>He sees and controls only these, by the names he answers to.</CardDescription>
              <CardAction>
                <Button variant="outline" size="sm" onClick={() => setPicker((p) => ({ open: true, key: p.key + 1 }))}>
                  <Plus />
                  Add from Home Assistant
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {drift.length ? (
                <StatusLine tone="warning" action={{ label: `Add ${one ? 'it' : 'them'} to the list`, onClick: () => addPicked(drift) }}>
                  {`Home Assistant exposes ${one ? 'one thing' : `${drift.length} things`} to Assist that ${one ? 'is' : 'are'} not on this list. Saving a change to the list stops exposing ${one ? 'it' : 'them'}.`}
                </StatusLine>
              ) : null}
              {pending.length ? (
                <StatusLine tone="warning" action={{ label: 'Apply now', onClick: () => void applyNow() }}>
                  Home Assistant does not match this list yet. Applying it {joinNames(pending)}.
                </StatusLine>
              ) : null}
              {missing.length ? (
                <StatusLine tone="warning">
                  {`${missing.length === 1 ? 'One device on the list is' : `${missing.length} devices on the list are`} not in Home Assistant: ${joinNames(missing)}.`}
                </StatusLine>
              ) : null}
              {rows.length ? (
                <SearchField value={search} onChange={setSearch} placeholder="Search names or rooms" />
              ) : null}
              {!rows.length ? (
                <p className="text-sm text-muted-foreground">No devices yet. Add the ones he may see and control from Home Assistant.</p>
              ) : groups.length ? (
                <HAEntityTables
                  groups={groups}
                  onEdit={(r) => setEditing({ id: r.id, original: r.id, value: { ...r.entry } })}
                  onRemove={(r) => void remove(r)}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Nothing matches.</p>
              )}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
                <span className="min-w-0 flex-1 text-muted-foreground">
                  {domains.length
                    ? `Also shared by kind, every one of them: ${domains.map((d) => (domainCounts[d] ? `${kindName(d)} (${domainCounts[d].length})` : kindName(d))).join(', ')}`
                    : 'Nothing is shared by kind.'}
                </span>
                <Button variant="outline" size="sm" onClick={() => setSharing(true)}>
                  Change
                </Button>
              </div>
            </CardContent>
          </Card>
          {inSetup ? null : (
            <Card>
              <CardHeader>
                <CardTitle>What he can do there</CardTitle>
                <CardDescription>Home Assistant's own actions, which follow what you share above.</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {!savedOn ? null : toolsError ? (
                  <StatusLine tone="warning">{errorText(toolsError)}</StatusLine>
                ) : tools.data?.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Home Assistant offers no actions yet.</p>
                ) : actions.length ? (
                  <ActionList items={actions} details readOnly />
                ) : null}
                <p className="text-sm text-muted-foreground">These run on their own. To keep him away from a device, leave it off the list above.</p>
                <SectionForm page={page} path={VERIFY} />
              </CardContent>
            </Card>
          )}
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
          <Dialog open={sharing} onOpenChange={setSharing}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Shared by kind</DialogTitle>
              </DialogHeader>
              {/* The Connection card leaves it to this dialog, whose one field it is. */}
              <SectionForm page={page} path={HA} omit={allBut(page.schema, HA, ['expose_domains'])} />
              <DialogFooter>
                <Button onClick={() => setSharing(false)}>Done</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <ItemDialog
            open={editing !== null}
            onOpenChange={(open) => !open && setEditing(null)}
            title={editingRow ? heardName(editingRow) : editing?.original ?? 'Add by entity ID'}
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
            onSubmit={commit}
          >
            <SchemaForm
              schema={entitySchema}
              value={editing?.value}
              onChange={(value) => setEditing((e) => e && { ...e, value: value as ExposedEntity })}
            />
            {theirs.length ? (
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap gap-1">
                  {theirs.map((a) => (
                    <Badge key={a} variant="outline">
                      {a}
                    </Badge>
                  ))}
                </div>
                <p className="text-sm text-muted-foreground">From Home Assistant: kept. Edit them there.</p>
              </div>
            ) : null}
            <p className="text-sm text-muted-foreground">Sent to Home Assistant when you save the page.</p>
          </ItemDialog>
        </>
      ) : null}
    </>
  )
}
