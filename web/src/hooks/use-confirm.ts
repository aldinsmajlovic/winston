import { createContext, useContext } from 'react'

export type Choice = { label: string; value: string; destructive?: boolean }
export type ConfirmRequest = {
  title: string
  /** What the action costs, one line each ("llm: about 3 min"). */
  lines?: string[]
  choices: Choice[]
}
export type Ask = (request: ConfirmRequest) => Promise<string | null>

export const ConfirmContext = createContext<Ask>(() => Promise.resolve(null))

/** Ask once before a slow or destructive action; resolves with the chosen value or null. */
export function useConfirm(): Ask {
  return useContext(ConfirmContext)
}
