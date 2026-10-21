import type { TurnMark } from '@/api/client'
import { $api } from '@/api/client'

export type MarkKind = TurnMark['kind']

/** Each kind of mistake as you would say it, for the flag's menu and its toast. */
export const MARK_LABEL: Record<MarkKind, string> = {
  misheard: 'He misheard me',
  not_addressed: "I wasn't talking to him",
  wrong_action: 'He did the wrong thing',
  too_slow: 'He was too slow',
}

/** The same, said back to you, for the line under a marked turn. */
const MARK_TOLD: Record<MarkKind, string> = {
  misheard: 'He misheard you',
  not_addressed: "You weren't talking to him",
  wrong_action: 'He did the wrong thing',
  too_slow: 'He was too slow',
}

export const markedKey = $api.queryOptions('get', '/api/turns/marked').queryKey

/** "Warm please" -> "Warm please.", "Warm, please." stays. */
const sentence = (text: string) => (/[.!?]["')]*$/.test(text) ? text : `${text}.`)

/** What a mark says, for its tooltip and History: 'He misheard you. You said "Warm, please."'. What you
 * wanted is quoted as written; a mark made by voice was written by him, so it says he noted it. */
export function markText(mark: TurnMark): string {
  const parts = [sentence(MARK_TOLD[mark.kind])]
  if (mark.said) parts.push(sentence(`You said "${mark.said}"`))
  if (mark.wanted) parts.push(sentence(`${mark.by === 'voice' ? 'He noted' : 'Wanted'}: "${mark.wanted}"`))
  return parts.join(' ')
}
