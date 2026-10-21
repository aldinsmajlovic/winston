import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode, type UIEvent } from 'react'
import {
  ArrowDown,
  ArrowUp,
  BellRing,
  CircleAlert,
  CircleCheck,
  CircleX,
  LoaderCircle,
  MessageSquarePlus,
  Mic,
  ShieldQuestion,
  Volume2,
} from 'lucide-react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { $api, type TurnMark } from '@/api/client'
import { useConfig } from '@/api/config'
import { micHolder, presence, useChat, useLive, useStartOver, type Item } from '@/api/live'
import { partName, useStatus } from '@/api/status'
import { IconButton } from '@/components/icon-button'
import { useTitle } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { ToolLine } from '@/components/tool-line'
import { TurnMarkControl } from '@/components/turn-mark'
import { WinstonHeader } from '@/components/winston-header'
import { Input } from '@/components/ui/input'
import { useMarks } from '@/hooks/use-marks'
import { useNow } from '@/hooks/use-now'
import { useRegion } from '@/hooks/use-region'
import { useRoom, useRoomChoices, type RoomChoice as Room } from '@/hooks/use-room'
import { useSpeech } from '@/hooks/use-speech'
import { useVoice } from '@/hooks/use-voice'
import { capitalize, joinNames } from '@/lib/format'
import { cn } from '@/lib/utils'

const NO_ROOM = 'none' // the picker's value for no room; Radix wants a non-empty one
const GAP_SECS = 10 * 60 // a quiet spell this long gets a time line
const NEAR_BOTTOM_PX = 80 // a view this close to the bottom follows new lines

const sameRoom = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase()

/** One choice of room, with what choosing it does. */
function RoomChoice({ value, name, what }: { value: string; name: string; what: string }) {
  return (
    <SelectItem value={value}>
      <span className="flex flex-col">
        {name}
        <span className="text-muted-foreground">{what}</span>
      </span>
    </SelectItem>
  )
}

/** Which room this browser is in: its conversation, and with a box its microphone, are shared with that room.
 * Locked while you talk, since the microphone follows the room. */
function RoomPicker({ room, rooms, onChange, locked }: { room: string; rooms: Room[]; onChange: (room: string) => void; locked: boolean }) {
  const choices = !room || rooms.some((r) => sameRoom(r.name, room)) ? rooms : [{ name: room, box: false }, ...rooms]
  const shown = room ? `Room: ${capitalize(room)}` : 'No room'
  return (
    <Select value={room || NO_ROOM} disabled={locked} onValueChange={(v) => onChange(v === NO_ROOM ? '' : v)}>
      <SelectTrigger
        aria-label="Room"
        size="sm"
        className="border-0 bg-transparent px-0 text-muted-foreground shadow-none hover:text-foreground disabled:cursor-default disabled:opacity-100 dark:bg-transparent dark:hover:bg-transparent"
      >
        <SelectValue>{locked ? `${shown}. Stop talking to change it.` : shown}</SelectValue>
      </SelectTrigger>
      <SelectContent position="popper" align="start" className="max-w-80">
        <RoomChoice value={NO_ROOM} name="No room" what="A typed conversation shared by screens without a room" />
        {choices.map((r) => (
          <RoomChoice
            key={r.name}
            value={r.name}
            name={capitalize(r.name)}
            what={r.box ? `Share the conversation and microphone with the ${r.name} box` : `Share the conversation with other screens there; "the lights" means its lights`}
          />
        ))}
      </SelectContent>
    </Select>
  )
}

/** Where the last finished sentence of a streaming reply ends, so speech can start before the rest
 * arrives; 0 while the first is still coming. */
function sentencesEnd(text: string): number {
  let end = 0
  for (const match of text.matchAll(/[.!?\n]+["')\]]*\s/g)) end = match.index + match[0].length
  return end
}

/** A restricted tool waiting for you: it runs only once confirmed here (plan §6 rule 4), never by voice. */
function Confirm({ item }: { item: Extract<Item, { kind: 'confirm' }> }) {
  const answer = $api.useMutation('post', '/api/confirmations/{cid}')
  const reply = (approve: boolean) => answer.mutate({ params: { path: { cid: item.id } }, body: { approve } })
  const done = item.status !== 'waiting'
  const Icon = !done ? ShieldQuestion : item.status === 'ok' ? CircleCheck : CircleX
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Icon className={cn('size-4 shrink-0', item.status === 'error' ? 'text-destructive' : 'text-muted-foreground')} />
      <span className={done ? 'text-muted-foreground' : 'font-medium'}>{item.label}</span>
      {done ? (
        <span className="text-muted-foreground">{item.outcome}</span>
      ) : (
        <>
          <span className="text-muted-foreground">Waits for your OK</span>
          <span className="flex gap-2">
            <Button size="sm" disabled={answer.isPending} onClick={() => reply(true)}>
              Confirm
            </Button>
            <Button size="sm" variant="outline" disabled={answer.isPending} onClick={() => reply(false)}>
              Decline
            </Button>
          </span>
        </>
      )}
    </div>
  )
}

/** Centred muted words between two rules: a new conversation, and why. */
function Divider({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-3 text-sm text-muted-foreground">
      <Separator className="flex-1" />
      <span className="shrink-0">{children}</span>
      <Separator className="flex-1" />
    </div>
  )
}

/** "New conversation after 30 min of quiet", "New conversation: settings were saved" (channels/web.py). */
const newConversation = (why: string) => (!why ? 'New conversation' : why.startsWith('after ') ? `New conversation ${why}` : `New conversation: ${why}`)

/** A new conversation drawn as a divider: the server's reason, or one started with New conversation. */
const isDivider = (item: Item) =>
  item.kind === 'conversation' || (item.kind === 'tool' && item.name.split('__').pop() === 'start_over' && item.status !== 'error')

const SPOKEN = ['voice', 'satellite']

/** Where an extension's message went, after "from <extension>". */
const VIA: Record<string, string> = {
  speakers: 'on the speakers',
  phone: 'sent to your phone',
  discord: 'sent on Discord',
  conversation: 'in the conversation',
  silent: 'shown here only',
}

function Line({ item, marks, time }: { item: Item; marks: Map<string, TurnMark>; time: (ts: number) => string }) {
  switch (item.kind) {
    case 'user':
      // A spoken line gets a flag to mark it as his mistake once its turn is kept (the turn record).
      return (
        <div className="group flex max-w-[75%] items-center gap-1 self-end">
          {item.turn && <TurnMarkControl turnId={item.turn} heard={item.text} mark={marks.get(item.turn)} />}
          <div className="flex min-w-0 items-center gap-2 rounded-2xl bg-secondary px-4 py-2.5 text-base">
            {SPOKEN.includes(item.channel) && <Mic aria-label="Spoken" className="size-3.5 shrink-0 text-muted-foreground" />}
            <span className="whitespace-pre-wrap">{item.text}</span>
          </div>
        </div>
      )
    case 'reply':
      return item.text.trim() ? <p className="max-w-[85%] text-lg/relaxed whitespace-pre-wrap">{item.text.trim()}</p> : null
    case 'tool':
      return isDivider(item) ? <Divider>New conversation</Divider> : <ToolLine name={item.name} label={item.label} status={item.status} icon={item.icon} />
    case 'conversation':
      return <Divider>{newConversation(item.text)}</Divider>
    case 'error':
      return (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-destructive">
          <CircleAlert className="size-4 shrink-0" />
          <span>{item.text}</span>
          <Link to="/status" className="text-foreground underline underline-offset-4">
            Status
          </Link>
        </div>
      )
    case 'confirm':
      return <Confirm item={item} />
    case 'spoke': {
      // Said on the speakers or in a conversation, his own words follow as a reply; otherwise they are here.
      const words = !['speakers', 'conversation'].includes(item.via)
      const source = [item.from && `from ${item.from}`, VIA[item.via] ?? item.via].filter(Boolean).join(', ')
      return (
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-sm text-muted-foreground">
          <BellRing className="size-4 shrink-0 translate-y-0.5" />
          <span>{time(item.ts)}</span>
          {words ? <span className="min-w-0 flex-1 text-base text-foreground">{item.text}</span> : null}
          <span className={cn(words && 'basis-full pl-6.5')}>{source}</span>
          {item.links.length > 0 && (
            <span className="flex basis-full flex-wrap gap-x-3 pl-6.5">
              {item.links.map((link) => (
                <a key={link.url} href={link.url} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-4">
                  {link.title}
                </a>
              ))}
            </span>
          )}
        </div>
      )
    }
  }
}

/** "Kitchen box", "Talk page microphone", "Discord": where a line was said, when not typed on a Talk page. */
const placeName = (channel: string, room?: string) =>
  channel === 'satellite' ? `${room ? capitalize(room) : 'Room'} box` : partName(channel)

/** Each line with what goes above it: the time after a quiet spell, and on the first line of a run from
 * another place, where it was said. A box's replies and actions name no room, so they continue the run its
 * words started. */
function annotate(items: Item[]): { item: Item; time?: number; place?: string }[] {
  let last = 0
  let run: { channel: string; room?: string } | undefined
  return items.map((item) => {
    const time = item.ts - last > GAP_SECS ? item.ts : undefined
    last = item.ts
    if (time || !('channel' in item) || isDivider(item)) run = undefined
    if (!('channel' in item) || isDivider(item)) return { item, time }
    const room = 'room' in item ? item.room : undefined
    if (run && run.channel === item.channel && (!room || !run.room || sameRoom(room, run.room))) {
      run.room ??= room
      return { item, time }
    }
    run = { channel: item.channel, room }
    return { item, time, place: item.channel === 'web' ? undefined : placeName(item.channel, room) }
  })
}

/** An empty conversation: how to start, and what he can use, from what is set up and working. */
function Empty({ uses, boxes, voice }: { uses: string[]; boxes: boolean; voice: boolean }) {
  const more = uses.length - 3
  return (
    <p className="m-auto max-w-md py-8 text-center text-muted-foreground">
      Type below{voice ? ', or press the microphone to talk' : ''}.{boxes ? ' In a room with a box, say its wake word.' : ''}{' '}
      {uses.length ? (
        `He can use ${more > 1 ? `${uses.slice(0, 3).join(', ')} and ${more} more` : joinNames(uses)}.`
      ) : (
        <>
          Nothing is connected yet.{' '}
          <Link to="/status" className="text-foreground underline underline-offset-4">
            Set him up
          </Link>
        </>
      )}
    </p>
  )
}

/** Template: Talk page. The conversation across every channel, his presence, a text field and the
 * talk button, with one line above them for the room and what matters now. */
export default function TalkPage() {
  useTitle()
  const feed = useLive()
  const marks = useMarks(feed.items.filter((i) => i.kind === 'tool' && i.name === 'mark_mistake' && i.status === 'ok').length)
  const chat = useChat()
  const startOver = useStartOver()
  const status = useStatus()
  const config = useConfig()
  const rooms = useRoomChoices()
  const region = useRegion()
  const [text, setText] = useState('')
  const now = useNow(10_000)
  const end = useRef<HTMLDivElement>(null)
  const [following, setFollowing] = useState(true) // the view is at the bottom, so new lines scroll it
  const [seen, setSeen] = useState(0) // how many lines there were when the view last left the bottom
  const onVoiceError = useCallback((message: string) => toast.error(message), [])
  const [room, setRoom] = useRoom()
  const { status: voiceStatus, start: startVoice, stop: stopVoice } = useVoice(onVoiceError, room)
  const speech = useSpeech(onVoiceError)
  const listened = useRef<{ since: number; said: Map<string, number> } | null>(null) // replies to what was typed here, and how much of each is spoken
  const heard = presence(feed, now)
  const state = speech.playing && heard === 'idle' ? 'speaking' : heard
  const name = status.data?.assistant ?? ''

  useEffect(() => {
    if (following) end.current?.scrollIntoView({ block: 'end' })
  }, [feed.items, following])

  const { on: readAloud, speak } = speech
  useEffect(() => {
    const turn = listened.current
    if (!readAloud || !turn) return
    feed.items.slice(turn.since).forEach((item) => {
      if (item.kind !== 'reply' || item.channel !== 'web') return
      const from = turn.said.get(item.id) ?? 0
      const to = item.done ? item.text.length : sentencesEnd(item.text)
      if (to <= from) return
      turn.said.set(item.id, to)
      speak(item.text.slice(from, to).trim())
    })
  }, [feed.items, readAloud, speak])

  const onScroll = (event: UIEvent<HTMLElement>) => {
    const el = event.currentTarget
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
    if (bottom || following) setSeen(feed.items.length) // at the bottom, or just leaving it: all seen so far
    setFollowing(bottom)
  }
  const toBottom = () => end.current?.scrollIntoView({ block: 'end' })

  const send = (event: FormEvent) => {
    event.preventDefault()
    const message = text.trim()
    if (!message) return
    if (readAloud) speech.prime()
    listened.current = { since: feed.items.length, said: new Map() }
    chat.mutate({ body: { text: message, room: room || null } })
    setText('')
    toBottom()
  }

  // The day goes with the time once the line is from another day.
  const when = (ts: number) =>
    new Date(ts * 1000).toDateString() === new Date(now).toDateString() ? region.time(ts) : `${region.day(ts)}, ${region.time(ts)}`

  const talking = voiceStatus !== 'off'
  const boxRoom = rooms.find((r) => sameRoom(r.name, room))?.box ?? false
  const voiceOff = config.data?.channels?.voice?.enabled === false
  const holder = voiceStatus === 'on' ? micHolder(feed, room) : undefined
  // The one fact that matters most right now, first match wins; the header already says when he cannot answer.
  const fact = voiceOff ? (
    <>
      The Talk page microphone is off.{' '}
      <Link to="/ways-to-talk" className="text-foreground underline underline-offset-4">
        Ways to talk
      </Link>
    </>
  ) : holder ? (
    `Microphone paused while the ${room} ${holder} has it`
  ) : voiceStatus === 'on' ? (
    'Listening. He hears everything until you press the microphone again.'
  ) : readAloud ? (
    'Reading typed replies aloud'
  ) : null
  const ha = config.data?.providers?.home_assistant
  const uses = [
    ...(ha?.url && ha.enabled !== false ? ['Home Assistant'] : []),
    ...(status.data?.extensions ?? []).filter((x) => x.state === 'running').map((x) => x.name),
  ]
  const wakeWord = rooms.some((r) => r.box) && config.data?.channels?.satellite?.enabled === true

  return (
    <div className="flex h-svh flex-col items-center">
      <WinstonHeader name={name} state={state} />

      <div className="relative flex min-h-0 w-full flex-1 flex-col">
        <main onScroll={onScroll} className="flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full max-w-3xl flex-col gap-5 px-4 pb-4">
            {feed.items.length === 0 && status.data ? <Empty uses={uses} boxes={wakeWord} voice={!voiceOff} /> : null}
            {annotate(feed.items).map(({ item, time, place }) => (
              <Fragment key={item.id}>
                {time ? <p className="text-center text-sm text-muted-foreground">{when(time)}</p> : null}
                {place ? (
                  <div className="flex flex-col gap-1">
                    <span className={cn('text-sm text-muted-foreground', item.kind === 'user' && 'self-end')}>{place}</span>
                    <Line item={item} marks={marks} time={region.time} />
                  </div>
                ) : (
                  <Line item={item} marks={marks} time={region.time} />
                )}
              </Fragment>
            ))}
            <div ref={end} />
          </div>
        </main>
        {!following && feed.items.length > seen ? (
          <Button variant="secondary" size="sm" onClick={toBottom} className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full">
            <ArrowDown data-icon="inline-start" />
            New messages
          </Button>
        ) : null}
      </div>

      {rooms.length > 0 || room || fact ? (
        <div className="flex w-full max-w-3xl flex-wrap items-center gap-x-4 gap-y-1 px-4 pt-3 text-sm text-muted-foreground">
          {rooms.length > 0 || room ? <RoomPicker room={room} rooms={rooms} onChange={setRoom} locked={talking} /> : null}
          {fact ? <span className="ml-auto">{fact}</span> : null}
        </div>
      ) : null}

      <form onSubmit={send} className="flex w-full max-w-3xl items-center gap-3 px-4 pt-3 pb-8">
        <IconButton
          type="button"
          size="icon"
          label={room ? `New conversation for ${room}${boxRoom ? ' and its box' : ''}` : 'New conversation'}
          disabled={startOver.isPending}
          onClick={() =>
            startOver.mutate({ body: { room: room || null } }, { onError: () => toast.error('Could not start a new conversation') })
          }
          className="size-14 rounded-full text-muted-foreground hover:text-foreground"
        >
          <MessageSquarePlus className="size-6" />
        </IconButton>
        <div className="relative flex-1">
          <Input
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={room ? `Message ${name} in the ${room}` : `Message ${name}`}
            aria-label="Message"
            className="h-14 rounded-full border-0 bg-card pr-14 pl-6 text-base shadow-none md:text-base"
          />
          <IconButton
            type="button"
            size="icon"
            label="Read typed replies aloud"
            aria-pressed={readAloud}
            onClick={speech.toggle}
            className="absolute top-1/2 right-2 -translate-y-1/2 rounded-full text-muted-foreground aria-pressed:bg-accent aria-pressed:text-foreground"
          >
            <Volume2 className="size-5" />
          </IconButton>
        </div>
        {text.trim() ? (
          <IconButton type="submit" size="icon" variant="default" label="Send" className="size-14 rounded-full">
            <ArrowUp className="size-6" />
          </IconButton>
        ) : (
          <IconButton
            type="button"
            size="icon"
            label={talking ? 'Stop talking' : 'Talk'}
            aria-pressed={talking}
            variant={talking ? 'secondary' : 'default'}
            disabled={voiceStatus === 'connecting' || (voiceOff && !talking)}
            onClick={() => void (talking ? stopVoice() : startVoice())}
            className="size-14 rounded-full"
          >
            {voiceStatus === 'connecting' ? <LoaderCircle className="size-6 animate-spin" /> : <Mic className="size-6" />}
          </IconButton>
        )}
      </form>
    </div>
  )
}
