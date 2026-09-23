import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ArrowUp,
  Bell,
  BookmarkPlus,
  CircleAlert,
  Clock,
  House,
  Lightbulb,
  LoaderCircle,
  MessageCircleQuestion,
  Mic,
  MicOff,
  Search,
  Send,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { $api, errorText } from '@/api/client'
import { presence, useChat, useLive, type Item, type PresenceState } from '@/api/live'
import { Presence } from '@/components/presence'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useVoice } from '@/hooks/use-voice'
import { cn } from '@/lib/utils'

const STATE_LABEL: Record<PresenceState, string> = {
  idle: 'Here if you need me',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
}

const TOOL_ICONS: [(base: string) => boolean, LucideIcon][] = [
  [(b) => b.startsWith('hass'), Lightbulb],
  [(b) => b === 'getlivecontext' || b === 'get_sensor_readings', House],
  [(b) => b === 'getdatetime', Clock],
  [(b) => b === 'web_search', Search],
  [(b) => b === 'ask_claude', MessageCircleQuestion],
  [(b) => b === 'remember', BookmarkPlus],
  [(b) => b === 'send_message', Send],
]

function ToolIcon({ name, running }: { name: string; running: boolean }) {
  if (running) return <LoaderCircle className="size-4 shrink-0 animate-spin" />
  const base = name.split('__').pop()?.toLowerCase() ?? ''
  const Icon = TOOL_ICONS.find(([match]) => match(base))?.[1] ?? Wrench
  return <Icon className="size-4 shrink-0" />
}

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

function Line({ item }: { item: Item }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className="flex max-w-[75%] items-center gap-2 self-end rounded-2xl bg-secondary px-4 py-2.5 text-base">
          {item.channel === 'voice' && <Mic aria-label="Spoken" className="size-3.5 shrink-0 text-muted-foreground" />}
          <span className="whitespace-pre-wrap">{item.text}</span>
        </div>
      )
    case 'reply':
      return item.text.trim() ? <p className="max-w-[85%] text-lg/relaxed whitespace-pre-wrap">{item.text.trim()}</p> : null
    case 'tool':
      return (
        <div className={cn('flex items-center gap-2 text-sm', item.status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
          <ToolIcon name={item.name} running={item.status === 'running'} />
          <span>{item.label}</span>
        </div>
      )
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
  }
}

/** Template: Talk page. The conversation across every channel, his presence, a text field and the
 * talk button. */
export default function TalkPage() {
  const feed = useLive()
  const chat = useChat()
  const status = $api.useQuery('get', '/api/status')
  const [text, setText] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const end = useRef<HTMLDivElement>(null)
  const onVoiceError = useCallback((message: string) => toast.error(message), [])
  const { status: voiceStatus, start: startVoice, stop: stopVoice } = useVoice(onVoiceError)
  const state = presence(feed, now)

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 10_000)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    void end.current?.scrollIntoView({ block: 'end' })
  }, [feed.items])

  const send = (event: FormEvent) => {
    event.preventDefault()
    const message = text.trim()
    if (!message) return
    chat.mutate({ body: { text: message } }, { onError: (error) => toast.error(errorText(error)) })
    setText('')
  }

  const talking = voiceStatus !== 'off'
  return (
    <div className="flex h-svh flex-col items-center">
      <header className="flex w-full max-w-3xl items-center gap-4 px-4 pt-8 pb-6">
        <Presence state={state} className="size-13" />
        <div className="flex flex-col">
          <h1 className="text-xl font-bold">{status.data?.assistant ?? 'Winston'}</h1>
          <span className="text-muted-foreground">{STATE_LABEL[state]}</span>
        </div>
        <nav className="ml-auto flex gap-6">
          <Link to="/status" className="text-muted-foreground hover:text-foreground">
            Settings
          </Link>
        </nav>
      </header>

      <main className="w-full flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 pb-4">
          {feed.items.map((item) => (
            <Line key={item.id} item={item} />
          ))}
          <div ref={end} />
        </div>
      </main>

      <form onSubmit={send} className="flex w-full max-w-3xl items-center gap-3 px-4 pt-3 pb-8">
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
