import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { $api, type Status } from './client'

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
