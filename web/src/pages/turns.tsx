import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Mic, NotebookPen, Play, Square } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import { $api, type TurnRecord } from '@/api/client'
import { useConfig } from '@/api/config'
import { presence, useLive } from '@/api/live'
import { partName } from '@/api/status'
import { IconButton } from '@/components/icon-button'
import { LoadError, useTitle } from '@/components/page'
import { TurnMarkControl } from '@/components/turn-mark'
import { ToolLine } from '@/components/tool-line'
import { WinstonHeader } from '@/components/winston-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useNow } from '@/hooks/use-now'
import { useRegion } from '@/hooks/use-region'
import { capitalize } from '@/lib/format'
import { markText } from '@/lib/marks'

/** How a spoken request started, in words. */
const START: Record<string, string> = {
  wake: 'after the wake word',
  follow_up: 'follow-up, no wake word',
  barge_in: 'you talked over him',
  button: 'the button on the box',
  speech: 'in the browser',
}

/** How a request ended when it was not a plain answer, from your side. */
const OUTCOME: Record<string, string> = {
  nothing_heard: 'He heard nothing',
  silent: 'He said nothing',
  cut_short: 'You talked over him',
  interrupted: 'Interrupted',
  dismissed: 'You ended the conversation',
  failed: 'Something failed',
}

/** A YYYY-MM-DD day as a local date, for the Region's day format. */
function dayDate(day: string): Date {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(y, m - 1, d)
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
  const region = useRegion()
  const time = region.time(turn.ts)
  // The browser's microphone says where in its start ("in the browser"); a box names its room.
  const where = turn.room ? capitalize(turn.room) : turn.channel === 'voice' ? '' : turn.channel === 'satellite' ? 'Room box' : partName(turn.channel)
  const meta = [showDay ? `${region.day(dayDate(turn.day))}, ${time}` : time, where, START[turn.start] ?? turn.start]
  if (turn.first_audio_ms) meta.push(`answered in ${(turn.first_audio_ms / 1000).toFixed(1)} s`)
  const outcome = turn.outcome !== 'answered' ? OUTCOME[turn.outcome] ?? turn.outcome : null
  const playing = player.playing === turn.id
  return (
    <article className="flex flex-col gap-2">
      <span className="text-sm text-muted-foreground">{meta.filter(Boolean).join(', ')}</span>
      {turn.heard || turn.audio ? (
        <div className="group flex max-w-[75%] flex-col items-end gap-1 self-end">
          <div className="flex items-center gap-1">
            <TurnMarkControl turnId={turn.id} heard={turn.heard} mark={turn.mark} />
            <div className="flex min-w-0 items-center gap-2 rounded-2xl bg-secondary py-1.5 pr-4 pl-2 text-base">
              {turn.audio ? (
                <IconButton label={playing ? 'Stop' : 'Play what he heard'} onClick={() => player.toggle(turn.id)} className="rounded-full">
                  {playing ? <Square /> : <Play />}
                </IconButton>
              ) : (
                <Mic aria-label="Spoken" className="mx-2 size-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="py-1 whitespace-pre-wrap">{turn.heard}</span>
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

/** Template: History. Each spoken request of a day under his diary entry, with what he heard played back
 * and a flag to mark his mistakes; or every marked request. */
export default function TurnsPage() {
  useTitle('History')
  const feed = useLive()
  const status = $api.useQuery('get', '/api/status')
  const keepDays = useConfig().data?.logging?.turns?.keep_days ?? 14
  const region = useRegion()
  const [day, setDay] = useState<string | undefined>(undefined)
  // ?view=marked, so a link can open the marked list.
  const [params, setParams] = useSearchParams()
  const setMarkedOnly = (on: boolean) => setParams(on ? { view: 'marked' } : {}, { replace: true })
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
  // Recordings off and none kept: only the explanation, nothing to browse.
  const empty = data?.enabled === false && days.length === 0
  const markedOnly = !empty && params.get('view') === 'marked'
  const at = data ? days.indexOf(data.day) : -1
  const earlier = at >= 0 ? days[at + 1] : days[0]
  const later = at > 0 ? days[at - 1] : undefined
  const shown = markedOnly ? marked.data ?? [] : data?.turns ?? []
  const markedCount = marked.data?.length ?? 0
  const count = data?.turns.length ?? 0

  return (
    <div className="flex min-h-svh flex-col items-center">
      <WinstonHeader name={status.data?.assistant ?? ''} state={presence(feed, now)} />
      <main className="flex w-full max-w-3xl flex-1 flex-col gap-7 px-4 pb-8">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <h2 className="mr-auto text-xl font-semibold">History</h2>
            {empty ? null : (
              <div className="flex gap-1">
                <Button variant="ghost" size="sm" aria-pressed={!markedOnly} className="aria-pressed:bg-accent" onClick={() => setMarkedOnly(false)}>
                  By day
                </Button>
                <Button variant="ghost" size="sm" aria-pressed={markedOnly} className="aria-pressed:bg-accent" onClick={() => setMarkedOnly(true)}>
                  {`Marked (${markedCount})`}
                </Button>
              </div>
            )}
          </div>
          {!markedOnly && data && !empty ? (
            <div className="flex items-center gap-2">
              <IconButton label="Earlier day" disabled={!earlier} onClick={() => setDay(earlier)}>
                <ChevronLeft />
              </IconButton>
              <span className="min-w-28 text-center font-medium">{region.day(dayDate(data.day))}</span>
              <IconButton label="Later day" disabled={!later} onClick={() => setDay(later)}>
                <ChevronRight />
              </IconButton>
              <span className="ml-2 text-muted-foreground">{count === 1 ? '1 request' : `${count} requests`}</span>
            </div>
          ) : null}
        </div>
        {data?.enabled === false ? (
          <p className="-mt-3 text-muted-foreground">
            {`History keeps spoken requests only while recordings are on. Turning them on keeps your audio, what he heard, did and said, for ${keepDays} days on this computer. The diary and marking mistakes need it.`}{' '}
            <Link to="/assistant" className="text-foreground underline underline-offset-4">
              Turn on in Assistant
            </Link>
          </p>
        ) : null}

        {!data && turns.isError ? (
          <LoadError error={turns.error} retrying={turns.isFetching} onRetry={() => void turns.refetch()} />
        ) : !data ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-10 w-2/3 self-end rounded-2xl" />
            <Skeleton className="h-5 w-3/4" />
          </div>
        ) : (
          <>
            {!markedOnly && data.diary ? (
              <article className="flex flex-col gap-2">
                <span className="flex items-center gap-2 text-sm text-muted-foreground">
                  <NotebookPen className="size-4" />
                  His diary
                </span>
                <p className="text-lg/relaxed whitespace-pre-wrap">{data.diary}</p>
              </article>
            ) : null}
            {shown.length === 0 ? (
              markedOnly ? (
                <span className="text-muted-foreground">Nothing marked yet. The flag beside something you said marks a mistake.</span>
              ) : data.enabled ? (
                <span className="text-muted-foreground">Nothing on this day.</span>
              ) : null
            ) : (
              shown.map((t) => <Turn key={t.id} turn={t} showDay={markedOnly} player={player} />)
            )}
          </>
        )}
      </main>
    </div>
  )
}
