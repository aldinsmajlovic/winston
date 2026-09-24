import { $api, type Config } from '@/api/client'
import { useConfig } from '@/api/config'
import { useDraft } from '@/hooks/use-draft'

/** The server a list comes from follows the draft, so switching an engine lists its models before saving. */
function baseUrl(name: string, config: Config | undefined): string | undefined {
  if (!config) return undefined
  if (name === 'llm_models') return config.llm.base_url
  if (name === 'voices') return config.tts?.base_url
  if (name === 'ha_entities' || name === 'ha_domains') {
    const ha = config.providers?.home_assistant
    return ha && ha.enabled !== false ? ha.url : undefined      // switched off: the server says so
  }
  return undefined
}

/** Live choices for a dropdown from `/api/options/{name}`. */
export function useOptions(name: string, query?: { domain?: string }) {
  const config = useConfig()
  const { draft } = useDraft()
  const base_url = baseUrl(name, draft ?? config.data)
  const result = $api.useQuery(
    'get',
    '/api/options/{name}',
    { params: { path: { name }, query: { base_url, domain: query?.domain } } },
    { staleTime: 30_000 },
  )
  const data = result.data
  return {
    choices: data?.choices ?? [],
    loading: result.isLoading,
    problem: data && !data.ok ? data.source : undefined,
  }
}
