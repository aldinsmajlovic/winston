import { useMemo, useState, type ReactNode } from 'react'
import type { Config } from '@/api/client'
import { DraftContext } from '@/hooks/use-draft'

export function DraftProvider({ children }: { children: ReactNode }) {
  const [draft, setDraft] = useState<Config | null>(null)
  const value = useMemo(() => ({ draft, setDraft }), [draft])
  return <DraftContext value={value}>{children}</DraftContext>
}
