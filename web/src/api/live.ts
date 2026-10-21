import { useEffect, useReducer } from 'react'
import { $api, type Config, type LiveEvent, type Status } from './client'
import { restartCost } from './status'
import { formatTime, type Clock } from '@/lib/format'

export type PresenceState = NonNullable<LiveEvent['state']>

export const STATE_LABEL: Record<PresenceState, string> = {
  idle: 'Ready',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
}

/** A server timestamp (seconds) as a time of day, by the Region's clock (useRegion). */
export const clock = (ts: number, by?: Clock) => formatTime(ts, by)

/** Why he cannot answer: Winston cannot be reached (`failed`, the status request failing), or his brain's
 * engine is still loading, or stopped, failed or turned off (then Status has more). Nothing when he can, or
 * when his brain is a server Winston does not run. */
export function notReady(status: Status | undefined, config?: Config, failed = false): { text: string; starting: boolean } | undefined {
  if (failed) return { text: "Can't reach Winston", starting: false }
  const brain = status?.engines.find((e) => e.name === 'llm')
  if (!brain && config?.engines?.llm?.enabled === false) return { text: "Can't answer: his brain is off", starting: false }
  if (!brain || brain.state === 'healthy') return undefined
  if (brain.state === 'starting') return { text: `Starting: his brain is loading (${restartCost(brain)})`, starting: true }
  const why = { stopped: 'is stopped', error: 'has failed', unhealthy: 'is not responding' }[brain.state]
  return { text: `Can't answer: his brain ${why}`, starting: false }
}

// Every line has its server time; lines said somewhere have their channel, and a room when the event names one.
export type Item =
  | { kind: 'user'; id: string; ts: number; text: string; channel: string; room?: string; turn?: string } // turn: its id in the turn record, once kept
  | { kind: 'reply'; id: string; ts: number; text: string; done: boolean; channel: string; room?: string }
  | {
      kind: 'tool'
      id: string
      ts: number
      name: string
      label: string
      status: NonNullable<LiveEvent['status']>
      icon?: string | null
      channel: string
      room?: string
    }
  | { kind: 'error'; id: string; ts: number; text: string }
  | { kind: 'confirm'; id: string; ts: number; label: string; status: NonNullable<LiveEvent['status']>; outcome: string; channel: string }
  | { kind: 'conversation'; id: string; ts: number; text: string } // a new conversation starts; text says why
  // He spoke up: an announcement (from: who asked, via: '') or an extension's message (from: the extension,
  // via: where it went: speakers, phone, discord, conversation, silent).
  | { kind: 'spoke'; id: string; ts: number; text: string; from: string; via: string; links: { title: string; url: string }[] }

// Who asked for an announcement (core/announce.py, Announcement.source), in words.
const ASKED_BY: Record<string, string> = { home_assistant: 'Home Assistant', api: 'an app', cli: 'the command line' }

type Feed = {
  items: Item[]
  states: Record<string, { state: PresenceState; ts: number }>
  mics: Record<string, string> // room (lower case) -> who has its mic; absent when nobody has
}
type Action = { type: 'reset' } | { type: 'event'; event: LiveEvent }

const empty: Feed = { items: [], states: {}, mics: {} }
const STALE_SECS = 90 // a state older than this without a follow-up is treated as idle

function apply(feed: Feed, ev: LiveEvent): Feed {
  const id = ev.id ?? ''
  const ts = ev.ts ?? 0
  const room = ev.room ?? undefined
  const replace = (key: string, update: (item: Item) => Item) =>
    feed.items.map((item) => (item.id === key ? update(item) : item))
  switch (ev.type) {
    case 'state':
      return ev.state ? { ...feed, states: { ...feed.states, [ev.channel]: { state: ev.state, ts } } } : feed
    case 'user':
      return {
        ...feed,
        items: [...feed.items, { kind: 'user', id, ts, text: ev.text ?? '', channel: ev.channel, room, turn: ev.call ?? undefined }],
      }
    case 'turn': {
      // A kept turn: its id goes onto the channel's latest line you said, as the server's history does.
      const last = feed.items.findLastIndex((item) => item.kind === 'user' && item.channel === ev.channel)
      const line = feed.items[last]
      if (last < 0 || line.kind !== 'user' || line.turn) return feed
      return { ...feed, items: feed.items.map((item, i) => (i === last ? { ...line, turn: ev.call ?? undefined } : item)) }
    }
    case 'reply': {
      const key = ev.reply ?? id
      if (feed.items.some((item) => item.id === key)) {
        return { ...feed, items: replace(key, (item) => (item.kind === 'reply' ? { ...item, text: item.text + (ev.text ?? '') } : item)) }
      }
      return { ...feed, items: [...feed.items, { kind: 'reply', id: key, ts, text: ev.text ?? '', done: false, channel: ev.channel, room }] }
    }
    case 'reply_end':
      return { ...feed, items: replace(ev.reply ?? '', (item) => (item.kind === 'reply' ? { ...item, done: true } : item)) }
    case 'tool': {
      const key = ev.call ?? id
      const tool: Item = {
        kind: 'tool',
        id: key,
        ts,
        name: ev.name ?? '',
        label: ev.label ?? ev.name ?? '',
        status: ev.status ?? 'running',
        icon: ev.icon,
        channel: ev.channel,
        room,
      }
      return feed.items.some((item) => item.id === key)
        ? { ...feed, items: replace(key, () => tool) }
        : { ...feed, items: [...feed.items, tool] }
    }
    case 'conversation':
      return { ...feed, items: [...feed.items, { kind: 'conversation', id, ts, text: ev.text ?? '' }] }
    case 'error':
      return { ...feed, items: [...feed.items, { kind: 'error', id, ts, text: ev.text ?? '' }] }
    case 'announcement':
    case 'nudge': {
      // An announcement's channel is who asked for it; an extension's message is labelled "<extension>: <kind>".
      const nudge = ev.type === 'nudge'
      const spoke: Item = {
        kind: 'spoke',
        id,
        ts,
        text: ev.text ?? '',
        from: nudge ? (ev.label ?? '').split(': ')[0] : (ASKED_BY[ev.channel] ?? ev.channel),
        via: nudge ? (ev.name ?? '').split(':')[0] : '',
        links: (ev.links ?? []) as { title: string; url: string }[],
      }
      return { ...feed, items: [...feed.items, spoke] }
    }
    case 'confirm': {
      const key = ev.call ?? id
      const confirm: Item = {
        kind: 'confirm',
        id: key,
        ts,
        label: ev.label ?? '',
        status: ev.status ?? 'waiting',
        outcome: ev.text ?? '',
        channel: ev.channel,
      }
      return feed.items.some((item) => item.id === key)
        ? { ...feed, items: replace(key, () => confirm) }
        : { ...feed, items: [...feed.items, confirm] }
    }
    case 'mic': {
      const mics = { ...feed.mics }
      const room = (ev.room ?? '').toLowerCase()
      if (ev.held_by) mics[room] = ev.held_by
      else delete mics[room]
      return { ...feed, mics }
    }
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

/** Who has the room's mic (a box that woke up), or undefined when nobody has or there is no room. */
export function micHolder(feed: Feed, room: string): string | undefined {
  return room ? feed.mics[room.replace(/\s+/g, ' ').toLowerCase()] : undefined
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

/** New conversation: the room's, for every box and screen in it, or the household's typed one. */
export function useStartOver() {
  return $api.useMutation('post', '/api/chat/start-over')
}
