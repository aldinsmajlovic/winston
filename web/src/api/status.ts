import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { $api, type EngineState, type EngineStatus, type Status } from './client'
import { duration } from '@/lib/format'

export const statusKey = $api.queryOptions('get', '/api/status').queryKey

// One stream for every part of the page that watches the status (the sidebar, the save controls).
let watchers = 0
let stream: EventSource | null = null

/** Live status: one fetch, then the server-sent stream keeps the cache current. */
export function useStatus() {
  const client = useQueryClient()
  const query = $api.useQuery('get', '/api/status', undefined, { refetchInterval: 15_000 })
  useEffect(() => {
    if (watchers++ === 0) {
      stream = new EventSource('/api/status/stream')
      stream.onmessage = (event) => client.setQueryData(statusKey, JSON.parse(event.data) as Status)
    }
    return () => {
      if (--watchers > 0) return
      stream?.close()
      stream = null
    }
  }, [client])
  return query
}

export function useEngineActions() {
  const client = useQueryClient()
  const refresh = { onSettled: () => client.invalidateQueries({ queryKey: statusKey }) }
  return {
    start: $api.useMutation('post', '/api/engines/{name}/start', refresh),
    stop: $api.useMutation('post', '/api/engines/{name}/stop', refresh),
    restart: $api.useMutation('post', '/api/engines/{name}/restart', refresh),
    startAll: $api.useMutation('post', '/api/engines/start_all', refresh),
    stopAll: $api.useMutation('post', '/api/engines/stop_all', refresh),
  }
}

// What each part of him is called (docs/ui-guide.md, vocabulary): on its own, and inside a sentence.
const PARTS: Record<string, [string, string]> = {
  llm: ['Brain', 'the brain'],
  tts: ['Speaking', 'the speaking voice'],
  stt: ['Hearing', 'hearing'],
  voice: ['Talk page microphone', 'the Talk page microphone'],
  satellite: ['Room boxes', 'the room boxes'],
  discord: ['Discord', 'Discord'],
}

/** "Brain" or "Room boxes": an engine or channel by its role; a custom engine by its name. */
export function partName(name: string): string {
  return PARTS[name]?.[0] ?? name
}

/** "the brain" or "the room boxes": how a confirm names what it restarts ("Restart the brain?"). */
export function engineLabel(part: string | { name: string }): string {
  const name = typeof part === 'string' ? part : part.name
  return PARTS[name]?.[1] ?? name
}

/** The one word for each state, beside its icon. */
export const STATE_WORDS: Record<EngineState, string> = {
  healthy: 'Running',
  starting: 'Starting',
  unhealthy: 'Not responding',
  stopped: 'Stopped',
  error: 'Failed',
}

/** An extension that should work and does not: it failed, can't run, or is on but stopped. Status lists
 * only extensions that are on, so one without `enabled` counts as on. */
export function notWorking(x: { enabled?: boolean; state: string }): boolean {
  return x.state === 'failed' || x.state === 'problem' || (x.enabled !== false && x.state === 'stopped')
}

/** Something to look at: a part that failed or stopped answering, or an extension that does not work.
 * Marks Status in the sidebar and his Settings link. */
export function needsAttention(status: Status | undefined): boolean {
  return (
    (status?.engines ?? []).some((e) => e.state === 'error' || e.state === 'unhealthy') ||
    (status?.extensions ?? []).some(notWorking)
  )
}

/** What a restart costs: how long it took last time, else its startup timeout. */
export function restartCost(e: EngineStatus): string {
  return e.started_in ? `about ${duration(e.started_in)}` : `up to ${duration(e.startup_timeout)}`
}

export const isRunning = (e: EngineStatus) => e.state !== 'stopped' && e.state !== 'error'

/** Saved settings not yet in effect: processes running the old ones (restartable here), stopped
 * ones that pick them up when started, and what only Winston's own restart applies. */
export function pendingRestarts(status: Status | undefined) {
  const changed = status?.engines.filter((e) => e.config_changed) ?? []
  return {
    running: changed.filter(isRunning),
    stopped: changed.filter((e) => !isRunning(e)),
    control: status?.pending_control ?? [],
  }
}
