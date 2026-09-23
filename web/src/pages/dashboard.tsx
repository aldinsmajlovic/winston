import { useCallback, useEffect, useState } from 'react'
import { Bell } from 'lucide-react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { STATE_LABEL, clock, lastChange, presence, useLive, type Item } from '@/api/live'
import { Presence } from '@/components/presence'
import { useVoice } from '@/hooks/use-voice'
import { useWakeLock } from '@/hooks/use-wake-lock'

const LINGER_SECS = 20 // the last exchange stays up this long after he goes idle
const ANNOUNCE_SECS = 60 // an announcement holds the screen this long unless tapped away
const TAIL = 240 // characters of a long reply kept on screen, the newest ones

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

/** Template: dashboard. Full screen for a wall tablet: the mark large (it is the talk button), one
 * line for his state, his words while he speaks, announcements taking the screen. */
export default function DashboardPage() {
  const feed = useLive()
  const [now, setNow] = useState(() => Date.now())
  const [dismissed, setDismissed] = useState('')
  const onVoiceError = useCallback((message: string) => toast.error(message), [])
  const { status: voice, start, stop } = useVoice(onVoiceError)
  useWakeLock()

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const state = presence(feed, now)
  const seconds = now / 1000
  const announcement = feed.items.findLast((item) => item.kind === 'announcement')
  const showAnnouncement =
    announcement?.kind === 'announcement' && announcement.id !== dismissed && seconds - announcement.ts < ANNOUNCE_SECS
  const recent = state !== 'idle' || seconds - lastChange(feed) < LINGER_SECS
  const { user, reply } = recent ? lastExchange(feed.items) : {}
  const line = voice === 'connecting' ? 'Connecting' : voice === 'off' && state === 'idle' ? 'Tap to talk' : STATE_LABEL[state]

  return (
    <div className="relative flex h-svh flex-col items-center overflow-hidden px-6 text-center">
      <Link to="/" className="absolute top-6 right-6 text-muted-foreground hover:text-foreground">
        Close
      </Link>

      {showAnnouncement ? (
        <button
          type="button"
          onClick={() => setDismissed(announcement.id)}
          aria-label="Dismiss the announcement"
          className="flex flex-1 flex-col items-center justify-center gap-8"
        >
          <Presence state={state} className="size-[min(22svh,40vw)]" />
          <span className="flex items-center gap-3 text-2xl text-muted-foreground">
            <Bell className="size-7" />
            {clock(announcement.ts)}
          </span>
          <p className="max-w-5xl text-6xl/tight font-semibold text-balance">{announcement.text}</p>
        </button>
      ) : (
        <>
          <div className="flex flex-1 items-end justify-center pt-16 pb-8">
            <button
              type="button"
              aria-label={voice === 'off' ? 'Talk' : 'Stop talking'}
              aria-pressed={voice !== 'off'}
              disabled={voice === 'connecting'}
              onClick={() => void (voice === 'off' ? start() : stop())}
              className="rounded-full outline-none focus-visible:ring-4 focus-visible:ring-ring"
            >
              <Presence state={state} className="size-[min(36svh,60vw)]" />
            </button>
          </div>
          <p className="text-3xl text-muted-foreground">{line}</p>
          <div className="flex max-w-5xl flex-1 flex-col items-center gap-4 pt-8">
            {user && <p className="text-2xl text-muted-foreground">{tail(user.text)}</p>}
            {reply && <p className="text-4xl/snug text-balance">{tail(reply.text.trim())}</p>}
          </div>
        </>
      )}
    </div>
  )
}
