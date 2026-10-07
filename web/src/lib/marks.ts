import type { TurnMark } from '@/api/client'
import { $api } from '@/api/client'

export type MarkKind = TurnMark['kind']

export const MARK_LABEL: Record<MarkKind, string> = {
  misheard: 'Misheard',
  not_addressed: 'Not talking to him',
  wrong_action: 'Wrong thing',
  too_slow: 'Too slow',
}

export const markedKey = $api.queryOptions('get', '/api/turns/marked').queryKey

/** What a mark says, for its tooltip and the Turns page: "Misheard: you said "Warm, please."". */
export function markText(mark: TurnMark): string {
  const parts = [MARK_LABEL[mark.kind]]
  if (mark.said) parts.push(`you said "${mark.said}"`)
  if (mark.wanted) parts.push(`you wanted ${mark.wanted}`)
  return parts.join(': ')
}
