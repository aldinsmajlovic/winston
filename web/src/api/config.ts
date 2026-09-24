import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { $api, fetchClient, type ConfigConflict, type ConfigResult } from './client'
import type { JsonSchema, Path } from '@/lib/schema'

/** One changed field for PATCH /api/config: a path from the config root; null removes the key. */
export type ConfigChange = { path: Path; value: unknown }

export const configKey = $api.queryOptions('get', '/api/config').queryKey
export const configVersionKey = $api.queryOptions('get', '/api/config/version').queryKey

/** "abc", W/"abc" or abc: the version an ETag names. */
const etagVersion = (etag: string | null) => etag?.replace(/^W\//, '').replace(/"/g, '') || undefined

/** The saved config. The version it was read at (its ETag) goes to the version cache in the same
 * step, so the two never disagree. */
export function useConfig() {
  const client = useQueryClient()
  return useQuery({
    queryKey: configKey,
    queryFn: async ({ signal }) => {
      const { data, error, response } = await fetchClient.GET('/api/config', { signal })
      if (!data) throw error ?? new Error('No config')
      const version = etagVersion(response.headers.get('ETag'))
      if (version) client.setQueryData(configVersionKey, { version })
      return data
    },
  })
}

/** The version the cached config was read at, for If-Match. */
export function configVersion(client: QueryClient): string | undefined {
  return client.getQueryData<{ version: string }>(configVersionKey)?.version
}

export function useSchema() {
  return $api.useQuery('get', '/api/config/schema', undefined, {
    staleTime: Infinity,
    select: (data) => data as unknown as JsonSchema,
  })
}

export type PatchOutcome =
  | { ok: true; result: ConfigResult }
  | { ok: false; status: 409; conflict: ConfigConflict }
  | { ok: false; status: number; error: unknown }

/** PATCH only the changed paths. With a version (If-Match) a path that also changed on disk since
 * then comes back as a 409 conflict instead of being overwritten; a dry run only reports restarts. */
export async function patchConfig(
  changes: ConfigChange[],
  options: { version?: string; dryRun?: boolean; client?: QueryClient },
): Promise<PatchOutcome> {
  const { data, error, response } = await fetchClient.PATCH('/api/config', {
    body: { changes },
    params: { query: { dry_run: options.dryRun ?? false } },
    headers: options.version ? { 'If-Match': `"${options.version}"` } : undefined,
  })
  if (data) {
    if (data.saved) options.client?.setQueryData(configVersionKey, { version: data.version })
    return { ok: true, result: data }
  }
  if (response.status === 409) return { ok: false, status: 409, conflict: error as ConfigConflict }
  return { ok: false, status: response.status, error }
}
