import { useEffect, useReducer } from 'react'
import { $api, type LiveEvent } from './client'

export type PresenceState = NonNullable<LiveEvent['state']>

export const STATE_LABEL: Record<PresenceState, string> = {
  idle: 'Idle',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
}

/** A server timestamp (seconds) as a local time of day. */
export const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

export type Item =
  | { kind: 'user'; id: string; text: string; channel: string }
  | { kind: 'reply'; id: string; text: string; done: boolean; channel: string }
  | { kind: 'tool'; id: string; name: string; label: string; status: NonNullable<LiveEvent['status']> }
  | { kind: 'announcement'; id: string; text: string; ts: number }
  | { kind: 'error'; id: string; text: string }

type Feed = { items: Item[]; states: Record<string, { state: PresenceState; ts: number }> }
type Action = { type: 'reset' } | { type: 'event'; event: LiveEvent }

const empty: Feed = { items: [], states: {} }
const STALE_SECS = 90 // a state older than this without a follow-up is treated as idle

function apply(feed: Feed, ev: LiveEvent): Feed {
  const id = ev.id ?? ''
  const replace = (key: string, update: (item: Item) => Item) =>
    feed.items.map((item) => (item.id === key ? update(item) : item))
  switch (ev.type) {
    case 'state':
      return ev.state ? { ...feed, states: { ...feed.states, [ev.channel]: { state: ev.state, ts: ev.ts ?? 0 } } } : feed
    case 'user':
      return { ...feed, items: [...feed.items, { kind: 'user', id, text: ev.text ?? '', channel: ev.channel }] }
    case 'reply': {
      const key = ev.reply ?? id
      if (feed.items.some((item) => item.id === key)) {
        return { ...feed, items: replace(key, (item) => (item.kind === 'reply' ? { ...item, text: item.text + (ev.text ?? '') } : item)) }
      }
      return { ...feed, items: [...feed.items, { kind: 'reply', id: key, text: ev.text ?? '', done: false, channel: ev.channel }] }
    }
    case 'reply_end':
      return { ...feed, items: replace(ev.reply ?? '', (item) => (item.kind === 'reply' ? { ...item, done: true } : item)) }
    case 'tool': {
      const key = ev.call ?? id
      const tool: Item = { kind: 'tool', id: key, name: ev.name ?? '', label: ev.label ?? ev.name ?? '', status: ev.status ?? 'running' }
      return feed.items.some((item) => item.id === key)
        ? { ...feed, items: replace(key, () => tool) }
        : { ...feed, items: [...feed.items, tool] }
    }
    case 'announcement':
      return { ...feed, items: [...feed.items, { kind: 'announcement', id, text: ev.text ?? '', ts: ev.ts ?? 0 }] }
    case 'error':
      return { ...feed, items: [...feed.items, { kind: 'error', id, text: ev.text ?? '' }] }
    default:
      return feed
  }
}

function reducer(feed: Feed, action: Action): Feed {
  return action.type === 'reset' ? empty : apply(feed, action.event)
}

/** Winston's presence: the newest state any channel reported, idle once it goes stale. */
export function presence(feed: Feed, now: number): PresenceState {
  const latest = Object.values(feed.states).sort((a, b) => b.ts - a.ts)[0]
  if (!latest || (latest.state !== 'idle' && now / 1000 - latest.ts > STALE_SECS)) return 'idle'
  return latest.state
}

/** When any channel last changed state, in server seconds; 0 if none has. */
export function lastChange(feed: Feed): number {
  return Math.max(0, ...Object.values(feed.states).map((s) => s.ts))
}

/** The live feed: recent history on connect, then events as they happen. A reconnect resends the
 * history, so the view starts over from it. */
export function useLive() {
  const [feed, dispatch] = useReducer(reducer, empty)
  useEffect(() => {
    const source = new EventSource('/api/live')
    source.onopen = () => dispatch({ type: 'reset' })
    source.onmessage = (event) => dispatch({ type: 'event', event: JSON.parse(event.data) as LiveEvent })
    return () => source.close()
  }, [])
  return feed
}

export function useChat() {
  return $api.useMutation('post', '/api/chat')
}
