import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { $api, type TurnMark } from '@/api/client'
import { markedKey } from '@/lib/marks'

/** Every marked turn by its id, for pages that show turns from the live feed (Talk). Read again when
 * `changed` changes: a mark made by voice (mark_mistake) shows without a reload. */
export function useMarks(changed?: unknown): Map<string, TurnMark> {
  const client = useQueryClient()
  const marked = $api.useQuery('get', '/api/turns/marked')
  useEffect(() => {
    if (changed) void client.invalidateQueries({ queryKey: markedKey })
  }, [changed, client])
  return new Map((marked.data ?? []).flatMap((t) => (t.mark ? [[t.id, t.mark] as const] : [])))
}
