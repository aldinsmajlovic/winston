import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Mic, Play, Square } from 'lucide-react'
import { Link } from 'react-router'
import { $api, type TurnRecord } from '@/api/client'
import { presence, useLive } from '@/api/live'
import { IconButton } from '@/components/icon-button'
import { LoadError } from '@/components/page'
import { TurnMarkControl } from '@/components/turn-mark'
import { ToolLine } from '@/components/tool-line'
import { WinstonHeader } from '@/components/winston-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useNow } from '@/hooks/use-now'
import { capitalize } from '@/lib/format'
import { markText } from '@/lib/marks'
import { cn } from '@/lib/utils'

const START: Record<string, string> = {
  wake: 'Wake word', follow_up: 'Follow-up', barge_in: 'Talked over him', button: 'Button', speech: '',
}

/** How a turn ended when it was not a plain answer. */
const OUTCOME: Record<string, string> = {
  silent: 'He said nothing',
  dismissed: 'Ended the conversation',
  nothing_heard: 'Nothing heard',
  cut_short: 'Cut short',
  interrupted: 'Interrupted',
  failed: 'Failed',
}

/** "Tue 7 Oct" for a YYYY-MM-DD day, in the browser's language. */
function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}

/** One turn's audio at a time. */
function usePlayer() {
  const audio = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  useEffect(() => () => audio.current?.pause(), [])
  const toggle = (id: string) => {
    audio.current?.pause()
    if (playing === id) return setPlaying(null)
    const a = new Audio(`/api/turns/${id}/audio`)
    a.onended = a.onerror = () => setPlaying((p) => (p === id ? null : p))
    audio.current = a
    setPlaying(id)
    void a.play()
  }
  return { playing, toggle }
}

function Turn({ turn, showDay, player }: { turn: TurnRecord; showDay: boolean; player: ReturnType<typeof usePlayer> }) {
  const time = new Date(turn.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const where = turn.room ? capitalize(turn.room) : turn.channel === 'voice' ? 'Browser' : capitalize(turn.channel)
  const meta = [showDay ? `${dayLabel(turn.day)}, ${time}` : time, where, START[turn.start] ?? turn.start]
  if (turn.first_audio_ms) meta.push(`first word ${(turn.first_audio_ms / 1000).toFixed(1)} s`)
  const outcome = turn.outcome !== 'answered' ? OUTCOME[turn.outcome] ?? turn.outcome : null
  return (
    <article className="flex flex-col gap-2">
      <div className="flex items-center gap-1 text-sm text-muted-foreground">
        <span>{meta.filter(Boolean).join(', ')}</span>
        {turn.audio ? (
          <IconButton label={player.playing === turn.id ? 'Stop' : 'Play what he heard'} onClick={() => player.toggle(turn.id)}>
            {player.playing === turn.id ? <Square /> : <Play />}
          </IconButton>
        ) : null}
      </div>
      {turn.heard ? (
        <div className="group flex max-w-[75%] flex-col items-end gap-1 self-end">
          <div className="flex items-center gap-1">
            <TurnMarkControl turnId={turn.id} heard={turn.heard} mark={turn.mark} />
            <div className="flex min-w-0 items-center gap-2 rounded-2xl bg-secondary px-4 py-2.5 text-base">
              <Mic aria-label="Spoken" className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="whitespace-pre-wrap">{turn.heard}</span>
            </div>
          </div>
          {turn.raw ? <span className="text-sm text-muted-foreground">{`Heard first as "${turn.raw}"`}</span> : null}
          {turn.mark ? <span className="text-sm text-muted-foreground">{markText(turn.mark)}</span> : null}
        </div>
      ) : null}
      {(turn.tools ?? []).map((tool, i) => (
        <ToolLine key={i} name={tool.name} label={tool.label || tool.name} status={tool.ok === false ? 'error' : 'ok'} icon={tool.icon} />
      ))}
      {turn.said ? <p className="max-w-[85%] text-lg/relaxed whitespace-pre-wrap">{turn.said}</p> : null}
      {outcome ? <span className="text-sm text-muted-foreground">{outcome}</span> : null}
    </article>
  )
}

/** Template: the turn record. Each spoken turn of a day as a conversation, with what he heard played back,
 * and a flag to mark his mistakes; or every marked turn. */
export default function TurnsPage() {
  const feed = useLive()
  const status = $api.useQuery('get', '/api/status')
  const [day, setDay] = useState<string | undefined>(undefined)
  const [markedOnly, setMarkedOnly] = useState(false)
  const turns = $api.useQuery('get', '/api/turns', { params: { query: { day } } })
  const marked = $api.useQuery('get', '/api/turns/marked')
  const player = usePlayer()
  const now = useNow(10_000)
  // A turn kept while the page is open shows up: the live feed says when one was.
  const kept = feed.items.filter((i) => i.kind === 'user' && i.turn).length
  const { refetch } = turns
  useEffect(() => {
    if (kept) void refetch()
  }, [kept, refetch])

  const data = turns.data
  const days = data?.days ?? []
  const at = data ? days.indexOf(data.day) : -1
  const earlier = at >= 0 ? days[at + 1] : days[0]
  const later = at > 0 ? days[at - 1] : undefined
  const shown = markedOnly ? marked.data ?? [] : data?.turns ?? []
  const markedCount = marked.data?.length ?? 0

  return (
    <div className="flex min-h-svh flex-col items-center">
      <WinstonHeader name={status.data?.assistant ?? ''} state={presence(feed, now)} />
      <main className="flex w-full max-w-3xl flex-1 flex-col gap-7 px-4 pb-8">
        <div className="flex items-center gap-2">
          {markedOnly ? (
            <span className="font-medium">Marked</span>
          ) : (
            <>
              <IconButton label="Earlier day" disabled={!earlier} onClick={() => setDay(earlier)}>
                <ChevronLeft />
              </IconButton>
              <span className="min-w-28 text-center font-medium">{data ? dayLabel(data.day) : ''}</span>
              <IconButton label="Later day" disabled={!later} onClick={() => setDay(later)}>
                <ChevronRight />
              </IconButton>
              {data ? <span className="ml-2 text-muted-foreground">{`${data.turns.length} turns`}</span> : null}
            </>
          )}
          <Button
            variant="ghost"
            aria-pressed={markedOnly}
            onClick={() => setMarkedOnly((m) => !m)}
            className={cn('ml-auto', markedOnly ? 'text-foreground' : 'text-muted-foreground')}
          >
            {markedOnly ? 'All turns' : `Marked (${markedCount})`}
          </Button>
        </div>
        {data?.enabled === false ? (
          <span className="-mt-3 text-muted-foreground">
            Turns are not being kept. Turn it on in{' '}
            <Link to="/system" className="underline underline-offset-4 hover:text-foreground">
              System
            </Link>
            .
          </span>
        ) : null}

        {!data && turns.isError ? (
          <LoadError error={turns.error} retrying={turns.isFetching} onRetry={() => void turns.refetch()} />
        ) : !data ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-10 w-2/3 self-end rounded-2xl" />
            <Skeleton className="h-5 w-3/4" />
          </div>
        ) : shown.length === 0 ? (
          <span className="text-muted-foreground">{markedOnly ? 'Nothing marked yet.' : 'None yet.'}</span>
        ) : (
          shown.map((t) => <Turn key={t.id} turn={t} showDay={markedOnly} player={player} />)
        )}
      </main>
    </div>
  )
}
