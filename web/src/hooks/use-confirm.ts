import { createContext, useContext } from 'react'

export type Choice = { label: string; value: string; destructive?: boolean }
export type ConfirmRequest = {
  title: string
  /** What the action costs, one line each ("llm: about 3 min"). */
  lines?: string[]
  choices: Choice[]
  /** One option that rides along with the choice, drawn as a checkbox under the lines ("Change them
   * to Mira?"), `checked` at first. `answer` gets its final state just before the dialog resolves. */
  check?: { label: string; detail?: string[]; checked: boolean; answer: (checked: boolean) => void }
}
export type Ask = (request: ConfirmRequest) => Promise<string | null>

export const ConfirmContext = createContext<Ask>(() => Promise.resolve(null))

/** Ask once before a slow or destructive action; resolves with the chosen value or null. */
export function useConfirm(): Ask {
  return useContext(ConfirmContext)
}
