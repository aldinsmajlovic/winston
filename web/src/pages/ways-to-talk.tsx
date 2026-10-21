import { useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { Plus } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { toast } from 'sonner'
import { $api } from '@/api/client'
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

/** The Talk page (its microphone, a wall display, when a typed conversation starts over), the room boxes
 * and Discord. How the browser takes turns is config.yaml only. */
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
    </SettingsPage>
  )
}
