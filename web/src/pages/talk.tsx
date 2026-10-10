import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ArrowUp,
  Bell,
  BellRing,
  CircleAlert,
  CircleCheck,
  CircleX,
  House,
  LoaderCircle,
  MessageSquarePlus,
  Mic,
  MicOff,
  ShieldQuestion,
  Volume2,
  VolumeOff,
} from 'lucide-react'
import { toast } from 'sonner'
import { $api, type TurnMark } from '@/api/client'
import { clock, micHolder, presence, useChat, useLive, useStartOver, type Item } from '@/api/live'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToolLine } from '@/components/tool-line'
import { TurnMarkControl } from '@/components/turn-mark'
import { WinstonHeader } from '@/components/winston-header'
import { Input } from '@/components/ui/input'
import { useMarks } from '@/hooks/use-marks'
import { useNow } from '@/hooks/use-now'
import { useRoom } from '@/hooks/use-room'
import { useSpeech } from '@/hooks/use-speech'
import { useVoice } from '@/hooks/use-voice'
import { capitalize } from '@/lib/format'
import { cn } from '@/lib/utils'

const NO_ROOM = 'none' // the picker's value for no room; Radix wants a non-empty one

/** Which room this browser is in: its conversation and mic are shared with that room's box. */
function RoomPicker({ room, onChange, disabled }: { room: string; onChange: (room: string) => void; disabled: boolean }) {
  const rooms = $api.useQuery('get', '/api/rooms')
  const names = rooms.data?.map((r) => r.name) ?? []
  if (!room && names.length === 0) return null
  const known = names.some((n) => n.toLowerCase() === room.toLowerCase())
  return (
    <Select value={room || NO_ROOM} disabled={disabled} onValueChange={(v) => onChange(v === NO_ROOM ? '' : v)}>
      <SelectTrigger
        aria-label="Room"
        title={disabled ? 'Stop talking to change the room' : 'The room you are in'}
        className="border-0 bg-transparent text-base text-muted-foreground shadow-none hover:text-foreground dark:bg-transparent"
      >
        <House className="size-4" />
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        <SelectItem value={NO_ROOM}>No room</SelectItem>
        {!known && room && <SelectItem value={room}>{room}</SelectItem>}
        {names.map((n) => (
          <SelectItem key={n} value={n}>
            {capitalize(n)}
          </SelectItem>
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
        <span className="flex gap-2">
          <Button size="sm" disabled={answer.isPending} onClick={() => reply(true)}>
            Confirm
          </Button>
          <Button size="sm" variant="outline" disabled={answer.isPending} onClick={() => reply(false)}>
            Decline
          </Button>
        </span>
      )}
    </div>
  )
}

const SPOKEN = ['voice', 'satellite']

function Line({ item, marks }: { item: Item; marks: Map<string, TurnMark> }) {
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
      return <ToolLine name={item.name} label={item.label} status={item.status} />
    case 'announcement':
      return (
        <div className="flex items-center gap-2.5 text-sm text-muted-foreground">
          <Bell className="size-4 shrink-0" />
          <span>{clock(item.ts)}</span>
          <span className="text-base font-semibold text-foreground">{item.text}</span>
        </div>
      )
    case 'error':
      return (
        <div className="flex items-center gap-2 text-sm text-destructive">
          <CircleAlert className="size-4 shrink-0" />
          <span>{item.text}</span>
        </div>
      )
    case 'confirm':
      return <Confirm item={item} />
    case 'nudge':
      // Spoken or mentioned, his own words follow as a reply; otherwise its plain line is shown here.
      return (
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-muted-foreground">
          <BellRing className="size-4 shrink-0" />
          <span>{clock(item.ts)}</span>
          <span>
            {item.label}, {VIA[item.via] ?? item.via}
          </span>
          {!['speakers', 'conversation'].includes(item.via) && <span className="basis-full pl-6.5 text-base text-foreground">{item.text}</span>}
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

/** Where a nudge went, after its extension and kind. */
const VIA: Record<string, string> = {
  speakers: 'said on the speakers',
  phone: 'sent to your phone',
  discord: 'sent on Discord',
  conversation: 'mentioned in the conversation',
  silent: 'shown here only',
}

/** Template: Talk page. The conversation across every channel, his presence, a text field and the
 * talk button. */
export default function TalkPage() {
  const feed = useLive()
  const marks = useMarks(feed.items.filter((i) => i.kind === 'tool' && i.name === 'mark_mistake' && i.status === 'ok').length)
  const chat = useChat()
  const startOver = useStartOver()
  const status = $api.useQuery('get', '/api/status')
  const [text, setText] = useState('')
  const now = useNow(10_000)
  const end = useRef<HTMLDivElement>(null)
  const onVoiceError = useCallback((message: string) => toast.error(message), [])
  const [room, setRoom] = useRoom()
  const { status: voiceStatus, start: startVoice, stop: stopVoice } = useVoice(onVoiceError, room)
  const speech = useSpeech(onVoiceError)
  const listened = useRef<{ since: number; said: Map<string, number> } | null>(null) // replies to what was typed here, and how much of each is spoken
  const heard = presence(feed, now)
  const state = speech.playing && heard === 'idle' ? 'speaking' : heard

  useEffect(() => {
    void end.current?.scrollIntoView({ block: 'end' })
  }, [feed.items])

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

  const send = (event: FormEvent) => {
    event.preventDefault()
    const message = text.trim()
    if (!message) return
    if (readAloud) speech.prime()
    listened.current = { since: feed.items.length, said: new Map() }
    chat.mutate({ body: { text: message, room: room || null } })
    setText('')
  }

  const talking = voiceStatus !== 'off'
  const paused = talking && micHolder(feed, room)
  return (
    <div className="flex h-svh flex-col items-center">
      <WinstonHeader
        name={status.data?.assistant ?? 'Winston'}
        state={state}
        actions={
          <>
            <RoomPicker room={room} onChange={setRoom} disabled={talking} />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Read replies aloud"
              aria-pressed={readAloud}
              title={readAloud ? 'Reading replies aloud' : 'Read replies aloud'}
              onClick={speech.toggle}
              className={cn('rounded-full', !readAloud && 'text-muted-foreground')}
            >
              {readAloud ? <Volume2 className="size-5" /> : <VolumeOff className="size-5" />}
            </Button>
          </>
        }
      />

      <main className="w-full flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 pb-4">
          {feed.items.map((item) => (
            <Line key={item.id} item={item} marks={marks} />
          ))}
          <div ref={end} />
        </div>
      </main>

      {paused && <p className="w-full max-w-3xl px-8 text-sm text-muted-foreground">Mic paused while the {paused} has it</p>}

      <form onSubmit={send} className="flex w-full max-w-3xl items-center gap-3 px-4 pt-3 pb-8">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="New conversation"
          title={room ? `New conversation, for everything in ${room}` : 'New conversation'}
          disabled={startOver.isPending}
          onClick={() =>
            startOver.mutate({ body: { room: room || null } }, { onError: () => toast.error('Could not start a new conversation') })
          }
          className="size-14 rounded-full text-muted-foreground hover:text-foreground"
        >
          <MessageSquarePlus className="size-6" />
        </Button>
        <Input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={`Message ${status.data?.assistant ?? 'Winston'}`}
          aria-label="Message"
          className="h-14 flex-1 rounded-full border-0 bg-card px-6 text-base shadow-none md:text-base"
        />
        {text.trim() ? (
          <Button type="submit" size="icon" aria-label="Send" className="size-14 rounded-full">
            <ArrowUp className="size-6" />
          </Button>
        ) : (
          <Button
            type="button"
            size="icon"
            aria-label={talking ? 'Stop talking' : 'Talk'}
            aria-pressed={talking}
            variant={talking ? 'secondary' : 'default'}
            disabled={voiceStatus === 'connecting'}
            onClick={() => void (talking ? stopVoice() : startVoice())}
            className="size-14 rounded-full"
          >
            {voiceStatus === 'connecting' ? (
              <LoaderCircle className="size-6 animate-spin" />
            ) : talking ? (
              <MicOff className="size-6" />
            ) : (
              <Mic className="size-6" />
            )}
          </Button>
        )}
      </form>
    </div>
  )
}
