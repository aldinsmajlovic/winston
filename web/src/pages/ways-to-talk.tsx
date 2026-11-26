import { useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { Plus } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { toast } from 'sonner'
import { $api, errorText } from '@/api/client'
import { useConfig } from '@/api/config'
import type { components } from '@/api/schema'
import { useStatus } from '@/api/status'
import { ItemDialog } from '@/components/item-dialog'
import { PageSkeleton } from '@/components/page'
import { RowMenu } from '@/components/row-menu'
import { SchemaForm, type FormContext } from '@/components/schema-form'
import { SectionForm } from '@/components/section-form'
import { StateIcon } from '@/components/state-icon'
import { DataTable, type Column } from '@/components/templates/list-page'
import { SettingsPage } from '@/components/templates/settings-page'
import { Button } from '@/components/ui/button'
import { KindIcon } from '@/components/kind-icon'
import { StatusLine } from '@/components/status-line'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useConfirm } from '@/hooks/use-confirm'
import { useRegion } from '@/hooks/use-region'
import { fold, useRoomChoices } from '@/hooks/use-room'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { capitalize } from '@/lib/format'
import { applyForm, isRecord, prune, section, setIn, type JsonSchema } from '@/lib/schema'

type SatelliteDevice = components['schemas']['SatelliteDevice']

const VOICE = ['channels', 'voice']
const WEB = ['channels', 'web']
const DISCORD = ['channels', 'discord']
const ASSIST = ['channels', 'assist']
const HA_INTEGRATION = ['channels', 'ha_integration']
const SATELLITE = ['channels', 'satellite']
const DEVICES = [...SATELLITE, 'devices']
const NOT_DEVICES = ['devices']
const NO_ROOM = 'none' // the picker's value for no room; Radix wants a non-empty one

/** A channel's state beside its card title while it is not Running. A channel that is off is not in the
 * status, and shows nothing. */
function ChannelState({ name }: { name: string }) {
  const status = useStatus()
  const channel = status.data?.engines.find((e) => e.kind === 'channel' && e.name === name)
  return channel && channel.state !== 'healthy' ? <StateIcon state={channel.state} detail={channel.detail} /> : null
}

/** A wall display's address: set to a room, or to none when the room is empty. */
const wallPath = (room: string) => `/wall?room=${encodeURIComponent(room)}`

/** Copy a wall display's link. A browser that gives no clipboard (a plain http address on the network)
 * gets the link in the toast instead. */
async function copyWallLink(room: string) {
  const link = location.origin + wallPath(room)
  try {
    await navigator.clipboard.writeText(link)
    toast.success('Link copied')
  } catch {
    toast(`Copy this link: ${link}`)
  }
}

/** A row under the Talk page's switch: a link that opens the Talk page full screen on a tablet, in a room. */
function WallDisplay() {
  const rooms = useRoomChoices()
  const [room, setRoom] = useState('')
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor="wall-room" className="mr-auto">
          Wall display
        </Label>
        <Select value={room || NO_ROOM} onValueChange={(v) => setRoom(v === NO_ROOM ? '' : v)}>
          <SelectTrigger id="wall-room" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_ROOM}>No room</SelectItem>
            {rooms.map((r) => (
              <SelectItem key={r.name} value={r.name}>
                {capitalize(r.name)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" asChild>
          <Link to={wallPath(room)}>Open</Link>
        </Button>
        <Button variant="outline" size="sm" onClick={() => void copyWallLink(room)}>
          Copy link
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">Full screen for a tablet on the wall: tap his logo to talk.</p>
    </div>
  )
}

type Editing = { room: string; original?: string; value: unknown }

/** The box schema with its key stored under a variable of the room's own (VOICE_PE_KEY_KITCHEN) while
 * none is named, so two boxes never share one. */
function ownKey(device: JsonSchema, room: string): JsonSchema {
  const field = device.properties?.key_env
  const meta = field?.secret
  if (!field || !room || !isRecord(meta) || typeof meta.default !== 'string') return device
  const name = `${meta.default}_${room.toUpperCase().replace(/[^A-Z0-9]/g, '_')}`
  return { ...device, properties: { ...device.properties, key_env: { ...field, secret: { ...meta, default: name } } } }
}

type DialogProps = {
  page: SettingsState
  editing: Editing | null
  setEditing: Dispatch<SetStateAction<Editing | null>>
  existing: string[]
  onDone: () => void
}

/** Add or edit a room box: its room, then the box's own fields. A pasted key writes its variable's name
 * into the box, as it does in a section form. */
function BoxDialog({ page, editing, setEditing, existing, onDone }: DialogProps) {
  const room = editing?.room.trim() ?? ''
  const at = [...DEVICES, room]
  const options = { config: setIn(page.value, at, editing?.value), path: at }
  // Pruned on every change, but handed to rjsf as the same object while it reads the same.
  const pruned = JSON.stringify(prune(ownKey(section(page.schema, at), room), page.schema, options))
  const schema = useMemo(() => JSON.parse(pruned) as JsonSchema, [pruned])
  const context = useMemo<FormContext>(
    () => ({ setAt: (path, value) => setEditing((e) => e && { ...e, value: setIn(e.value, path.slice(DEVICES.length + 1), value) }) }),
    [setEditing],
  )
  const host = isRecord(editing?.value) ? editing.value.host : undefined
  return (
    <ItemDialog
      open={editing !== null}
      onOpenChange={(open) => !open && setEditing(null)}
      title={editing?.original ? capitalize(editing.original) : 'Add a box'}
      keyLabel="Room"
      keyInfo={section(page.schema, DEVICES).description}
      keyValue={editing?.room ?? ''}
      onKeyChange={(r) => setEditing((e) => e && { ...e, room: r })}
      existing={existing}
      original={editing?.original}
      canSubmit={room !== '' && typeof host === 'string' && host.trim() !== ''}
      onSubmit={onDone}
    >
      <SchemaForm
        schema={schema}
        value={editing?.value}
        formContext={context}
        onChange={(next) => setEditing((e) => e && { ...e, value: applyForm(e.value, next, schema) })}
      />
    </ItemDialog>
  )
}

type Box = { room: string; device: SatelliteDevice }

/** The room boxes: their switch first, then each box with its live connection after the settings they
 * share. */
function RoomBoxes({ page }: { page: SettingsState }) {
  const saved = useConfig().data
  // Polled while the list shows, so a box that connects says so.
  const rooms = $api.useQuery('get', '/api/rooms', undefined, { refetchInterval: 5_000 })
  const region = useRegion()
  const ask = useConfirm()
  const navigate = useNavigate()
  const [editing, setEditing] = useState<Editing | null>(null)
  const devices = page.value.channels?.satellite?.devices ?? {}
  const setDevices = (next: Record<string, SatelliteDevice>) => page.update((c) => setIn(c, DEVICES, next))
  const off = !saved?.channels?.satellite?.enabled
  const help = section(page.schema, SATELLITE).help

  const when = (t: number) =>
    new Date(t * 1000).toDateString() === new Date().toDateString() ? region.time(t) : `${region.day(t)}, ${region.time(t)}`
  // As Status shows a part: Off in words, else the state's icon and its word.
  const state = (room: string) => {
    if (off) return <span className="text-muted-foreground">Off</span>
    if (!rooms.data) return null
    const r = rooms.data.find((x) => x.box && fold(x.name) === fold(room))
    if (r?.connected) return <StateIcon state="healthy" word="Connected" words="always" />
    if (r?.since) return <StateIcon state="unhealthy" word={`Offline since ${when(r.since)}`} words="always" />
    return <StateIcon state="stopped" word="Never connected" words="always" />
  }

  const edit = (b: Box) => setEditing({ room: b.room, original: b.room, value: b.device })
  const remove = async (room: string) => {
    const choice = await ask({
      title: `Remove the ${capitalize(room)} box?`,
      lines: ['He stops listening there when you save.'],
      choices: [{ label: 'Remove', value: 'yes', destructive: true }],
    })
    if (!choice) return
    const next = { ...devices }
    delete next[room]
    setDevices(next)
  }
  const done = () => {
    if (!editing) return
    const room = editing.room.trim()
    const next = { ...devices }
    if (editing.original !== undefined && editing.original !== room) delete next[editing.original]
    next[room] = editing.value as SatelliteDevice
    setDevices(next)
    setEditing(null)
  }

  const rows: Box[] = Object.entries(devices).map(([room, device]) => ({ room, device }))
  const columns: Column<Box>[] = [
    {
      key: 'room',
      title: 'Room',
      className: 'whitespace-normal',
      render: (b) => (
        <Button type="button" variant="link" className="h-auto p-0 text-left break-words whitespace-normal text-foreground" onClick={() => edit(b)}>
          {capitalize(b.room)}
        </Button>
      ),
    },
    { key: 'state', title: 'State', render: (b) => state(b.room) },
    {
      key: 'wake',
      title: 'Wake word',
      className: 'text-muted-foreground',
      // The ids the box listens for (okay_nabu,hey_jarvis) in words, as the field's help spells them.
      render: (b) =>
        b.device.wake_word ? b.device.wake_word.split(',').map((w) => w.trim().split('_').map(capitalize).join(' ')).join(', ') : "The box's own",
    },
    { key: 'area', title: 'Home Assistant area', className: 'text-muted-foreground', render: (b) => b.device.area ?? capitalize(b.room) },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Room boxes
          <ChannelState name="satellite" />
        </CardTitle>
        {typeof help === 'string' ? <CardDescription>{help}</CardDescription> : null}
        <CardAction>
          <Button variant="outline" size="sm" onClick={() => setEditing({ room: '', value: {} })}>
            <Plus data-icon="inline-start" />
            Add a box
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <SectionForm page={page} path={SATELLITE} omit={NOT_DEVICES}>
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(b) => b.room}
            actions={(b) => (
              <RowMenu
                name={capitalize(b.room)}
                items={[
                  { label: 'Edit', onSelect: () => edit(b) },
                  { label: 'Open wall display', onSelect: () => void navigate(wallPath(b.room)) },
                  { label: 'Copy wall display link', onSelect: () => void copyWallLink(b.room) },
                  { label: 'Remove', destructive: true, onSelect: () => void remove(b.room) },
                ]}
              />
            )}
            empty="No room boxes yet. Add one to talk to him in a room without a screen."
          />
        </SectionForm>
      </CardContent>
      <BoxDialog page={page} editing={editing} setEditing={setEditing} existing={Object.keys(devices)} onDone={done} />
    </Card>
  )
}

/** The Talk page (its microphone, a wall display, when a typed conversation starts over), the room boxes,
 * Discord and Home Assistant Assist. How the browser takes turns is config.yaml only. */
export default function WaysToTalkPage() {
  const s = useSettings()
  if (s.loading) return <PageSkeleton title="Ways to talk" />
  const { page } = s
  return (
    <SettingsPage title="Ways to talk" intro="The places and apps you talk to him through." page={page}>
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            Talk page
            <ChannelState name="voice" />
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SectionForm page={page} path={VOICE}>
            <WallDisplay />
          </SectionForm>
          <SectionForm page={page} path={WEB} />
        </CardContent>
      </Card>
      <RoomBoxes page={page} />
      <SectionForm page={page} path={DISCORD} title="Discord" status={<ChannelState name="discord" />} />
      <FromHomeAssistantCard page={page} />
    </SettingsPage>
  )
}

/** Home Assistant's two ways to talk to him in one card, with when to use which: the integration (installed
 * there and paired), then Assist (nothing to install, no password), each with its switch, its fields once on,
 * and its state. Also in the wizard's Home Assistant step. */
export function FromHomeAssistantCard({ page }: { page: SettingsState }) {
  const assistOn = page.value.channels?.assist?.enabled === true
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KindIcon name="homeassistant" />
          From Home Assistant
        </CardTitle>
        <CardDescription>
          Its app, chat and voice satellites can talk to him in either of two ways, and one is enough. The integration is
          installed there and paired, and also shows his state and his extensions' readings; Assist needs nothing installed.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <section aria-labelledby="ha-integration" className="flex flex-col gap-2">
          <h3 id="ha-integration" className="text-sm font-semibold">
            The integration
          </h3>
          <SectionForm page={page} path={HA_INTEGRATION}>
            <IntegrationStatus page={page} />
          </SectionForm>
        </section>
        <section aria-labelledby="ha-assist" className="flex flex-col gap-2 border-t pt-4">
          <h3 id="ha-assist" className="text-sm font-semibold">
            Assist
          </h3>
          <SectionForm page={page} path={ASSIST}>
            {assistOn ? (
              <StatusLine tone="warning">Anyone on your network can ask him to act. The integration limits it to a paired Home Assistant.</StatusLine>
            ) : null}
            <AssistStatus />
          </SectionForm>
        </section>
      </CardContent>
    </Card>
  )
}

/** One line on the saved state, polled while the page is open: reached, not yet (and where to add him),
 * or the port taken; and how many of Home Assistant's satellites he speaks on. Nothing while it is off. */
function AssistStatus() {
  const st = $api.useQuery('get', '/api/assist', undefined, { refetchInterval: 10_000 }).data
  const region = useRegion()
  if (!st?.enabled) return null
  const when = (t: number) =>
    new Date(t * 1000).toDateString() === new Date().toDateString() ? region.time(t) : `${region.day(t)}, ${region.time(t)}`
  const n = st.satellites ?? 0
  return (
    <div className="flex flex-col gap-1 text-sm">
      {st.error ? (
        <p className="text-destructive">{st.error}</p>
      ) : st.last_seen ? (
        <p>Home Assistant reached him at {when(st.last_seen)}.</p>
      ) : (
        <p className="text-muted-foreground">
          Not reached since Winston started. In Home Assistant: Settings, Devices and services, then Winston under Discovered, or add Wyoming Protocol with this computer's address and the port.
        </p>
      )}
      {st.listening && n > 0 ? (
        <p className="text-muted-foreground">
          Speaks on {n} Home Assistant {n === 1 ? 'satellite' : 'satellites'}.
        </p>
      ) : null}
    </div>
  )
}

/** Opens Winston's repository in that Home Assistant's Marketplace (HACS before 2026.11), to download from:
 * its own "My" redirect, which both know. */
function downloadLink(haUrl: string): string {
  const q = new URLSearchParams({ owner: 'aldinsmajlovic', repository: 'winston', category: 'integration' })
  return `${haUrl.replace(/\/+$/, '')}/_my_redirect/hacs_repository?${q}`
}

/** Getting him into Home Assistant and pairing: with Home Assistant connected, Download there and Add from
 * here, no code; else, or as well, the code to type there while one is open. Then whether a Home Assistant is
 * paired and last reached him, and forgetting it. Nothing while the switch is off. Add and Pair save first,
 * since the port opens on save (the wizard saves only at Continue). */
function IntegrationStatus({ page }: { page: SettingsState }) {
  const haUrl = page.value.providers?.home_assistant?.url ?? undefined
  const q = $api.useQuery('get', '/api/ha_integration', undefined, { refetchInterval: 5_000 })
  const pair = $api.useMutation('post', '/api/ha_integration/code', { onSuccess: () => q.refetch() })
  const forget = $api.useMutation('delete', '/api/ha_integration/pairing', { onSuccess: () => q.refetch() })
  const add = $api.useMutation('post', '/api/ha_integration/add', {
    onSuccess: () => {
      toast.success('Added in Home Assistant.')
      void q.refetch()
    },
    onError: (e) => toast.error(errorText(e)),
  })
  const ask = useConfirm()
  const region = useRegion()
  const st = q.data
  if (!page.value.channels?.ha_integration?.enabled || !st) return null
  if (st.error) return <p className="text-sm text-destructive">{st.error}</p>
  const saved = async () => !page.dirty || (await page.save())
  const when = (t: number) =>
    new Date(t * 1000).toDateString() === new Date().toDateString() ? region.time(t) : `${region.day(t)}, ${region.time(t)}`
  const unpair = async () => {
    const yes = await ask({
      title: 'Forget the paired Home Assistant?',
      lines: ['Its Winston entities go unavailable until you pair it again with a new code.'],
      choices: [{ label: 'Forget it', value: 'yes', destructive: true }],
    })
    if (yes) forget.mutate({}, { onSuccess: () => toast.success('Home Assistant forgotten.') })
  }
  return (
    <div className="flex flex-col gap-3 text-sm">
      {st.code ? (
        <div className="flex flex-col gap-1">
          <p>Type this code in Home Assistant, under Settings, Devices and services, Winston:</p>
          <p className="text-2xl font-semibold tracking-widest tabular-nums">{st.code}</p>
          <p className="text-muted-foreground">Works until {region.time(st.code_until ?? 0)}, once.</p>
        </div>
      ) : st.paired ? (
        <p>
          Paired.{' '}
          {st.last_seen ? `Home Assistant reached him at ${when(st.last_seen)}.` : 'Not reached since Winston started.'}
        </p>
      ) : haUrl ? (
        <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
          <li>Download opens Winston in Home Assistant's Marketplace (HACS before 2026.11). Download him there, then restart Home Assistant.</li>
          <li>Add to Home Assistant adds him there, with no code to type.</li>
        </ol>
      ) : (
        <p className="text-muted-foreground">
          Not paired. Download Winston from Home Assistant's Marketplace (HACS before 2026.11) and restart it, then select Pair and type the code there. Home Assistant finds him on its own, or asks for this computer's address and the port.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {haUrl && !st.paired && !st.code ? (
          <>
            <Button variant="outline" size="sm" asChild>
              <a href={downloadLink(haUrl)} target="_blank" rel="noreferrer">
                Download in Home Assistant
              </a>
            </Button>
            <Button size="sm" disabled={add.isPending || page.saving} onClick={() => void saved().then((ok) => ok && add.mutate({}))}>
              {add.isPending ? 'Adding...' : 'Add to Home Assistant'}
            </Button>
          </>
        ) : null}
        <Button variant="outline" size="sm" disabled={pair.isPending || page.saving} onClick={() => void saved().then((ok) => ok && pair.mutate({}))}>
          {st.code ? 'New code' : st.paired ? 'Pair again' : haUrl ? 'Pair with a code' : 'Pair'}
        </Button>
        {st.paired ? (
          <Button variant="outline" size="sm" disabled={forget.isPending} onClick={unpair}>
            Forget Home Assistant
          </Button>
        ) : null}
      </div>
    </div>
  )
}
