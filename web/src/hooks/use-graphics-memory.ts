import { keepPreviousData } from '@tanstack/react-query'
import { $api } from '@/api/client'
import type { SettingsState } from '@/hooks/use-settings'

/** The draft's graphics memory plan: the card, the brain's memory by context, the recommendation. Polled, since
 * other programs come and go; the memory bar and the context slider share it. */
export function useGraphicsMemory(page: SettingsState) {
  const body = { engine: 'llm', block: page.value.engines?.llm ?? null, model: page.value.llm.model }
  return $api.useQuery('post', '/api/graphics-memory', { body }, { placeholderData: keepPreviousData, refetchInterval: 15_000 }).data
}
