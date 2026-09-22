import { createContext, useContext, type Dispatch, type SetStateAction } from 'react'
import type { Config } from '@/api/client'

export type DraftState = { draft: Config | null; setDraft: Dispatch<SetStateAction<Config | null>> }

export const DraftContext = createContext<DraftState>({ draft: null, setDraft: () => {} })

/** The unsaved config, shared by every page so edits survive navigation until saved or discarded. */
export function useDraft(): DraftState {
  return useContext(DraftContext)
}
