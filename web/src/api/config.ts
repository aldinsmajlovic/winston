import { useQueryClient } from '@tanstack/react-query'
import { $api } from './client'
import type { JsonSchema } from '@/lib/schema'

export const configKey = $api.queryOptions('get', '/api/config').queryKey

export function useConfig() {
  return $api.useQuery('get', '/api/config')
}

export function useSchema() {
  return $api.useQuery('get', '/api/config/schema', undefined, {
    staleTime: Infinity,
    select: (data) => data as unknown as JsonSchema,
  })
}

export function useSaveConfig() {
  const client = useQueryClient()
  return $api.useMutation('put', '/api/config', {
    onSuccess: (result) => {
      if (result.saved) void client.invalidateQueries({ queryKey: configKey })
    },
  })
}
