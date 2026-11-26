import { keepPreviousData } from '@tanstack/react-query'
import { $api, type Config } from '@/api/client'

/** The draft's graphics memory plan: the card, the brain's memory by context, the recommendation. Polled, since
 * other programs come and go; the memory bar, the context slider and the model picker share it. */
export function useGraphicsMemory(config: Config | undefined) {
  const body = { engine: 'llm', block: config?.engines?.llm ?? null, model: config?.llm.model }
  return $api.useQuery('post', '/api/graphics-memory', { body }, { enabled: !!config, placeholderData: keepPreviousData, refetchInterval: 15_000 }).data
}
