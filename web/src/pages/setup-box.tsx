import { useEffect, useRef, useState } from 'react'
import { Lock } from 'lucide-react'
import { $api, errorText, type BoxJoined, type SetupBox } from '@/api/client'
import { StatusLine } from '@/components/status-line'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useOptions } from '@/hooks/use-options'
import { StepActions } from '@/components/step-actions'
import { useBusy } from '@/hooks/use-busy'
import { getIn, setIn } from '@/lib/schema'
import type { StepProps } from '@/pages/setup'

const SATELLITE = ['channels', 'satellite']
const OWN_ERRORS = { meta: { showsErrors: true } }     // each shows its error in the step
const OTHER = '\u0000other'                 // the "Another network" item: no SSID can be this
const STOCK_WAKE = [
  { value: 'okay_nabu', label: 'Okay Nabu' },
  { value: 'hey_jarvis', label: 'Hey Jarvis' },
  { value: 'hey_mycroft', label: 'Hey Mycroft' },
]
const FLASHING: Record<string, string> = { downloading: 'Downloading the firmware', writing: 'Writing the firmware', starting: 'Starting the box' }

type Props = Pick<StepProps, 'page' | 'next'>

/** The wizard's A room box (docs/setup.md): a Voice PE plugged in by USB-C gets Winston's firmware from this
 * release, Wi-Fi from the networks it sees, and a room. Each part shows once the one before it is done; the
 * box is polled, so plugging it in is enough. */
export function BoxStep({ page, next }: Props) {
  // Every 2 s for a box plugged in; twice a second while one is flashed, for its progress.
  const box = $api.useQuery('get', '/api/setup/box', {}, {
    refetchInterval: (q) => (q.state.data?.flash && !['done', 'error'].includes(q.state.data.flash.stage) ? 500 : 2_000),
  })
  const [busy, run] = useBusy()
  const [keepFirmware, setKeepFirmware] = useState(false)
  const [joined, setJoined] = useState<(BoxJoined & { ssid: string }) | null>(null)
  const skip = () => void run(() => next('skipped'))
  const port = box.data?.ports[0]

  if (!port) {
    return (
      <>
        <StatusLine tone="muted">Waiting for a Voice PE on USB-C.</StatusLine>
        <StepActions busy={busy} onSkip={skip} />
      </>
    )
  }
  const flash = box.data?.flash?.port === port ? box.data.flash : null
  if (flash?.stage !== 'done' && !keepFirmware) {
    return <Firmware port={port} state={box.data!} onStarted={() => void box.refetch()} onKeep={() => setKeepFirmware(true)} onSkip={skip} busy={busy} />
  }
  if (!joined) return <Wifi port={port} onJoined={setJoined} onSkip={skip} />
  return <Room page={page} next={next} joined={joined} onSkip={skip} />
}

type FirmwareProps = { port: string; state: SetupBox; onStarted: () => void; onKeep: () => void; onSkip: () => void; busy: boolean }

/** What the box runs, then Put Winston's firmware on it, with the flash's progress or why it stopped. */
function Firmware({ port, state, onStarted, onKeep, onSkip, busy }: FirmwareProps) {
  const start = $api.useMutation('post', '/api/setup/box/flash', OWN_ERRORS)
  const flash = state.flash?.port === port ? state.flash : null
  const running = !!flash && flash.stage !== 'done' && flash.stage !== 'error'
  const info = state.info
  const go = () => start.mutate({ body: { port } }, { onSuccess: onStarted })
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Its firmware</CardTitle>
          <CardDescription>{`Found a Voice PE on ${port}${info ? `, running ${info.firmware} ${info.version}` : ''}.`}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {info?.winston && !running ? <StatusLine tone="ok">It already runs Winston&apos;s firmware.</StatusLine> : null}
          {running ? (
            <div className="flex flex-col gap-2">
              <span className="text-sm">{`${FLASHING[flash.stage]}${flash.stage === 'starting' ? '' : `, ${flash.percent}%`}`}</span>
              <Progress value={flash.percent} aria-label={FLASHING[flash.stage]} />
            </div>
          ) : null}
          {flash?.stage === 'error' ? <StatusLine tone="error">{flash.error}</StatusLine> : null}
          {start.error ? <StatusLine tone="error">{errorText(start.error)}</StatusLine> : null}
          {!running ? (
            <p className="text-sm text-muted-foreground">Flashing erases the box, Wi-Fi and any key Home Assistant gave it included.</p>
          ) : null}
          {info?.winston && !running ? (
            <div>
              <Button variant="outline" size="sm" disabled={start.isPending} onClick={go}>
                Flash again
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
      <StepActions
        label={info?.winston ? 'Continue' : flash?.stage === 'error' ? 'Try again' : "Put Winston's firmware on it"}
        busy={busy || running || start.isPending}
        onClick={info?.winston ? onKeep : go}
        onSkip={onSkip}
      />
    </>
  )
}

type WifiProps = { port: string; onJoined: (j: BoxJoined & { ssid: string }) => void; onSkip: () => void }

/** The networks the box sees, or another one by name, its password, and Connect, which waits until the box
 * has joined and Winston has reached it there. */
function Wifi({ port, onJoined, onSkip }: WifiProps) {
  const scan = $api.useMutation('post', '/api/setup/box/networks', OWN_ERRORS)
  const join = $api.useMutation('post', '/api/setup/box/wifi', OWN_ERRORS)
  const [picked, setPicked] = useState('')
  const [typed, setTyped] = useState('')
  const [password, setPassword] = useState('')
  const look = scan.mutate
  useEffect(() => look({ body: { port } }), [look, port])
  const networks = scan.data ?? []
  const ssid = picked === OTHER ? typed.trim() : picked
  const secured = picked === OTHER || networks.find((n) => n.ssid === picked)?.secured !== false
  const connect = () =>
    join.mutate(
      { body: { port, ssid, password: secured ? password : '' } },
      { onSuccess: (data) => onJoined({ ...data, ssid }) },
    )
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Wi-Fi</CardTitle>
          <CardDescription>The network the box uses. Winston must be able to reach it there.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {scan.isPending ? <StatusLine tone="muted">Asking the box which networks it sees.</StatusLine> : null}
          {scan.error ? (
            <StatusLine tone="error" action={{ label: 'Look again', onClick: () => look({ body: { port } }) }}>
              {errorText(scan.error)}
            </StatusLine>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="box-network">Network</Label>
            <Select value={picked} onValueChange={setPicked}>
              <SelectTrigger id="box-network" className="w-full">
                <SelectValue placeholder="Pick a network" />
              </SelectTrigger>
              <SelectContent position="popper" className="max-w-(--radix-select-trigger-width)">
                {networks.map((n) => (
                  <SelectItem key={n.ssid} value={n.ssid}>
                    <span className="truncate">{n.ssid}</span>
                    {n.secured ? <Lock className="size-3.5 text-muted-foreground" aria-label="Needs a password" /> : null}
                  </SelectItem>
                ))}
                <SelectItem value={OTHER}>Another network</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">Only 2.4 GHz networks show: the box cannot use 5 GHz.</p>
          </div>
          {picked === OTHER ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="box-ssid">Network name</Label>
              <Input id="box-ssid" value={typed} maxLength={32} onChange={(e) => setTyped(e.target.value)} />
            </div>
          ) : null}
          {secured ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="box-password">Password</Label>
              <Input id="box-password" type="password" autoComplete="off" value={password} maxLength={63} onChange={(e) => setPassword(e.target.value)} />
            </div>
          ) : null}
          {join.isPending ? <StatusLine tone="muted">{`Joining ${ssid}. This takes up to a minute.`}</StatusLine> : null}
          {join.error ? <StatusLine tone="error">{errorText(join.error)}</StatusLine> : null}
        </CardContent>
      </Card>
      <StepActions label="Connect" busy={join.isPending || !ssid || (secured && !password)} onClick={connect} onSkip={onSkip} />
    </>
  )
}

type RoomProps = Props & { joined: BoxJoined & { ssid: string }; onSkip: () => void }

/** Its room, its Home Assistant area, its wake word when his name is not Winston (the box knows only Okay
 * Winston and the stock three), and Home Assistant's old entry for it. Adding saves it as a room box and
 * switches the room boxes on, as Ways to talk would. */
function Room({ page, next, joined, onSkip }: RoomProps) {
  const disable = $api.useMutation('post', '/api/setup/box/ha-disable', OWN_ERRORS)
  const areas = useOptions('ha_areas')
  const [busy, run] = useBusy()
  const [room, setRoom] = useState('')
  const [area, setArea] = useState('')
  const [wake, setWake] = useState(STOCK_WAKE[0].value)
  const [offEntry, setOffEntry] = useState(true)
  const [adding, setAdding] = useState('')
  const before = useRef<unknown>(null)
  const winston = (page.value.assistant?.name ?? 'Winston').trim().toLowerCase() === 'winston'
  const entry = joined.ha_entry && !joined.ha_entry.disabled ? joined.ha_entry : null
  // Once added, its own room is in the config: not "taken".
  const taken = !adding && Object.keys(page.value.channels?.satellite?.devices ?? {}).some((r) => r.toLowerCase() === room.trim().toLowerCase())

  // save() saves what the page shows, so it runs once the box is in the draft it renders.
  const added = !!adding && !!page.value.channels?.satellite?.devices?.[adding]
  useEffect(() => {
    if (!added) return
    void run(async () => {
      if (!(await page.save())) {
        page.update((c) => setIn(c, SATELLITE, before.current))     // cancelled or refused: as it was
        setAdding('')
        return
      }
      if (entry && offEntry) {
        try {
          await disable.mutateAsync({ body: { entry_id: entry.entry_id } })
        } catch {
          return                // the box is saved: its sentence shows, and Continue goes on
        }
      }
      await next('done')
    })
    // Once per press of the button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [added])

  const add = () => {
    const name = room.trim()
    before.current = getIn(page.value, SATELLITE)
    const device = { host: joined.host, wake_word: winston ? 'okay_winston' : wake, ...(area ? { area } : {}) }
    page.update((c) => setIn(setIn(c, [...SATELLITE, 'devices', name], device), [...SATELLITE, 'enabled'], true))
    setAdding(name)
  }
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Its room</CardTitle>
          <CardDescription>{`It joined ${joined.ssid}, and Winston reaches it at ${joined.host}.`}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="box-room">Room</Label>
            <Input id="box-room" value={room} placeholder="living room" onChange={(e) => setRoom(e.target.value)} />
            {taken ? <p className="text-sm text-destructive">A box is in that room already.</p> : null}
          </div>
          {areas.choices.length ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="box-area">Home Assistant area</Label>
              <Select value={area} onValueChange={setArea}>
                <SelectTrigger id="box-area" className="w-full">
                  <SelectValue placeholder="Same as the room's name" />
                </SelectTrigger>
                <SelectContent position="popper" className="max-w-(--radix-select-trigger-width)">
                  {areas.choices.map((c) => (
                    <SelectItem key={c.value} value={c.value}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">So &quot;the lights&quot; said there means that room&apos;s lights.</p>
            </div>
          ) : null}
          {!winston ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="box-wake">Wake word</Label>
              <Select value={wake} onValueChange={setWake}>
                <SelectTrigger id="box-wake" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  {STOCK_WAKE.map((w) => (
                    <SelectItem key={w.value} value={w.value}>
                      {w.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">The box knows Okay Winston and these three.</p>
            </div>
          ) : null}
          {entry ? (
            <div className="flex flex-col gap-2">
              <StatusLine tone="warning">Home Assistant had this box, and its entry there would come back as a new device.</StatusLine>
              <div className="flex items-center gap-3">
                <Switch id="box-ha-off" checked={offEntry} onCheckedChange={setOffEntry} />
                <Label htmlFor="box-ha-off">Disable it in Home Assistant</Label>
              </div>
            </div>
          ) : null}
          {disable.error ? <StatusLine tone="error">{errorText(disable.error)}</StatusLine> : null}
        </CardContent>
      </Card>
      {disable.error ? (
        <StepActions label="Continue" busy={busy} onClick={() => void run(() => next('done'))} />
      ) : (
        <StepActions label="Add the box and continue" busy={busy || page.saving || !room.trim() || taken} onClick={add} onSkip={onSkip} />
      )}
    </>
  )
}
