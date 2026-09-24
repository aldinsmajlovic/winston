import { $api, type Config, type Options } from '@/api/client'
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

/** The brain's draft engine block, for llm_models to list what that engine can load from the disk. */
function engine(name: string, config: Config | undefined): string | undefined {
  const e = name === 'llm_models' ? config?.engines?.llm : undefined
  if (!e) return undefined
  const { driver, image, args, cmd, url, volumes } = e
  return JSON.stringify({ driver, image, args, cmd, url, volumes })
}

/** The query a list is asked with for this config (the draft or the saved one). */
export function optionsParams(name: string, config: Config | undefined, query?: { domain?: string; q?: string }) {
  return {
    params: {
      path: { name },
      query: { base_url: baseUrl(name, config), domain: query?.domain, q: query?.q, engine: engine(name, config) },
    },
  }
}

/** Whether a restart of the brain has to download its model first: the engine's list of what it can load
 * from the disk is known (served_by) and the model is neither running nor there. */
export function mustDownload(list: Options | undefined, model: string): boolean {
  return !!list?.served_by && !list.choices.some((c) => c.value === model && !c.disabled)
}

/** The brain's restart cost when it downloads first; the Hub is never asked, so the size is not known. */
export const DOWNLOAD_COST = 'downloads the model first, size unknown, so it may take many minutes'

/** Live choices for a dropdown from `/api/options/{name}`. `q` asks for one value only (a pasted
 * Discord user id); `enabled: false` fetches nothing. */
export function useOptions(name: string, query?: { domain?: string; q?: string }, enabled = true) {
  const config = useConfig()
  const { draft } = useDraft()
  const result = $api.useQuery('get', '/api/options/{name}', optionsParams(name, draft ?? config.data, query), {
    staleTime: 30_000,
    enabled: enabled && name !== '',
  })
  const data = result.data
  return {
    choices: data?.choices ?? [],
    loading: result.isLoading,
    problem: data && !data.ok ? data.source : undefined,
    /** Where the list came from; for some lists a note to show (hint_words: what the engine does with them). */
    source: data?.ok ? data.source : undefined,
    /** llm_models: the engine the Downloaded group was read for; unset when nothing is known of downloads. */
    servedBy: data?.served_by ?? undefined,
  }
}
