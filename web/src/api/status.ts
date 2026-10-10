import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { $api, type EngineStatus, type Status } from './client'
import { duration } from '@/lib/format'

export const statusKey = $api.queryOptions('get', '/api/status').queryKey

/** Live status: one fetch, then the server-sent stream keeps the cache current. */
export function useStatus() {
  const client = useQueryClient()
  const query = $api.useQuery('get', '/api/status', undefined, { refetchInterval: 15_000 })
  useEffect(() => {
    const source = new EventSource('/api/status/stream')
    source.onmessage = (event) => client.setQueryData(statusKey, JSON.parse(event.data) as Status)
    return () => source.close()
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

/** "the voice channel" or "the llm engine": how a confirm names what it restarts. */
export function engineLabel(e: EngineStatus): string {
  return e.kind === 'channel' ? `the ${e.name} channel` : `the ${e.name} engine`
}

/** What a restart costs: how long it took last time, else its startup timeout. */
export function restartCost(e: EngineStatus): string {
  return e.started_in ? `about ${duration(e.started_in)}` : `up to ${duration(e.startup_timeout)}`
}

const running = (e: EngineStatus) => e.state !== 'stopped' && e.state !== 'error'

/** Saved settings not yet in effect: processes running the old ones (restartable here), stopped
 * ones that pick them up when started, and what only the next `winston up` applies. */
export function pendingRestarts(status: Status | undefined) {
  const changed = status?.engines.filter((e) => e.config_changed) ?? []
  return {
    running: changed.filter(running),
    stopped: changed.filter((e) => !running(e)),
    control: status?.pending_control ?? [],
  }
}
