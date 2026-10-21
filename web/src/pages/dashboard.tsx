import { useCallback, useState } from 'react'
import { BellRing } from 'lucide-react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { useConfig } from '@/api/config'
import { STATE_LABEL, lastChange, micHolder, notReady, presence, useLive, type Item } from '@/api/live'
import { useStatus } from '@/api/status'
import { useTitle } from '@/components/page'
import { Presence } from '@/components/presence'
import { useNow } from '@/hooks/use-now'
import { useRegion } from '@/hooks/use-region'
import { useRoom } from '@/hooks/use-room'
import { useVoice } from '@/hooks/use-voice'
import { useWakeLock } from '@/hooks/use-wake-lock'
import { capitalize } from '@/lib/format'

const LINGER_SECS = 20 // the last exchange stays up this long after he goes idle
const ANNOUNCE_SECS = 60 // an announcement holds the screen this long unless tapped away
const TAIL = 180 // characters of a long reply kept on screen, the newest ones

type Said = Extract<Item, { kind: 'user' | 'reply' }>

/** The newest thing you said and his answer to it, if he has started one. */
function lastExchange(items: Item[]): { user?: Said; reply?: Said } {
  const reply = items.findLastIndex((item) => item.kind === 'reply' && item.text.trim() !== '')
  const user = items.findLastIndex((item) => item.kind === 'user')
  return {
    user: user >= 0 ? (items[user] as Said) : undefined,
    reply: reply > user ? (items[reply] as Said) : undefined,
  }
}

const tail = (text: string) => (text.length > TAIL ? `...${text.slice(-TAIL).replace(/^\S*\s/, '')}` : text)

/** What he said aloud on his own takes the screen: an announcement, or a message on the speakers. */
const spokenUp = (item: Item) => item.kind === 'spoke' && (item.via === '' || item.via === 'speakers')

/** Template: wall display. Full screen for a wall tablet: the mark large (it is the talk button), its room,
 * one line for his state, his words while he speaks, what he says on his own taking the screen. */
export default function DashboardPage() {
  useTitle('Wall display')
  const feed = useLive()
  const status = useStatus()
  const config = useConfig()
  const region = useRegion()
  const now = useNow(1000)
  const [dismissed, setDismissed] = useState('')
  const onVoiceError = useCallback((message: string) => toast.error(message), [])
  const [room] = useRoom() // ?room=main room: a wall panel and its room's box share one conversation
  const { status: voice, start, stop } = useVoice(onVoiceError, room)
  useWakeLock()

  const state = presence(feed, now)
  const seconds = now / 1000
  const last = feed.items.findLastIndex(spokenUp)
  const said = feed.items.findLastIndex((item) => item.kind === 'user')
  const found = feed.items[last]
  // What he said on his own holds the screen until it is tapped away, answered, or a minute old.
  const announcement =
    found?.kind === 'spoke' && found.id !== dismissed && said < last && seconds - found.ts < ANNOUNCE_SECS ? found : undefined
  const recent = state !== 'idle' || seconds - lastChange(feed) < LINGER_SECS
  const { user, reply } = recent && !announcement ? lastExchange(feed.items) : {}
  const paused = voice === 'on' && micHolder(feed, room)
  const problem = notReady(status.data, config.data, status.isError)
  const voiceOff = config.data?.channels?.voice?.enabled === false
  const line =
    voice === 'connecting'
      ? 'Connecting'
      : problem
        ? problem.text
        : paused && state !== 'speaking'
          ? `Microphone paused while the ${paused} has it`
          : voice === 'off' && state === 'idle'
            ? voiceOff
              ? 'The Talk page microphone is off'
              : 'Tap to talk'
            : STATE_LABEL[state]

  // One composition for every view: the mark and its line fixed in the middle row, words in the row
  // below, the empty row above balancing them. Type sizes follow the screen, two steps only.
  return (
    <div className="relative grid h-svh grid-rows-[1fr_auto_1fr] justify-items-center overflow-hidden px-8 text-center">
      {room ? <span className="absolute top-4 left-4 px-4 py-3 text-lg text-muted-foreground">{capitalize(room)}</span> : null}
      <Link to="/" className="absolute top-4 right-4 px-4 py-3 text-lg text-muted-foreground hover:text-foreground">
        Close
      </Link>

      <div className="row-start-2 flex flex-col items-center gap-8">
        <button
          type="button"
          aria-label={voice === 'off' ? 'Talk' : 'Stop talking'}
          aria-pressed={voice !== 'off'}
          disabled={voice === 'connecting' || (voiceOff && voice === 'off')}
          onClick={() => void (voice === 'off' ? start() : stop())}
          className="rounded-full outline-none focus-visible:ring-4 focus-visible:ring-ring"
        >
          <Presence state={state} className="size-[min(32svh,50vw)]" />
        </button>
        {announcement ? (
          <span className="flex items-center gap-3 text-[clamp(1.125rem,2.2vmin,1.5rem)] text-muted-foreground">
            <BellRing className="size-[1em]" />
            {region.time(announcement.ts)}
          </span>
        ) : (
          <p className="text-[clamp(1.125rem,2.2vmin,1.5rem)] text-muted-foreground">{line}</p>
        )}
      </div>

      <div className="row-start-3 flex w-full max-w-[min(64rem,90vw)] flex-col items-center gap-4 overflow-hidden pt-8">
        {announcement ? (
          <button
            type="button"
            onClick={() => setDismissed(announcement.id)}
            aria-label="Dismiss"
            className="text-[clamp(1.75rem,4.2vmin,3rem)]/tight font-semibold text-balance"
          >
            {announcement.text}
          </button>
        ) : (
          <>
            {user && <p className="text-[clamp(1.125rem,2.2vmin,1.5rem)] text-muted-foreground">{tail(user.text)}</p>}
            {reply && <p className="text-[clamp(1.75rem,4.2vmin,3rem)]/tight text-balance">{tail(reply.text.trim())}</p>}
          </>
        )}
      </div>
    </div>
  )
}
