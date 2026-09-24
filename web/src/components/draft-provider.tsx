import { useCallback, useEffect, useMemo, useState, type ReactNode, type SetStateAction } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Config } from '@/api/client'
import { configVersion, useConfig, type ConfigChange } from '@/api/config'
import { clearChangedFieldErrors } from '@/api/field-errors'
import { DraftContext } from '@/hooks/use-draft'
import { useSecretsDraft } from '@/hooks/use-secrets'
import { applyChanges, diffConfig, DraftStoreContext, effectiveChanges } from '@/hooks/use-settings'

type Edits = { changes: ConfigChange[]; version: string | undefined }

/** The draft is kept as the changed paths over the saved config, so a save from elsewhere (a voice
 * switched by asking) shows through every field the draft did not touch. The version is the one the
 * first edit started from; Save sends it as If-Match. */
export function DraftProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const saved = useConfig().data
  const secrets = useSecretsDraft()
  const [state, setEdits] = useState<Edits>({ changes: [], version: undefined })
  const [saving, setSaving] = useState(false)

  const edits = useMemo(() => (saved ? effectiveChanges(saved, state.changes) : []), [saved, state.changes])
  const draft = useMemo(
    () => (saved && state.changes.length ? applyChanges(saved, state.changes) : null),
    [saved, state.changes],
  )

  const setDraft = useCallback(
    (action: SetStateAction<Config | null>) => {
      if (!saved) return
      const resolve = (before: Config) => (typeof action === 'function' ? action(before) : action)
      setEdits((e) => {
        const next = resolve(e.changes.length ? applyChanges(saved, e.changes) : saved)
        if (next === null) return { changes: [], version: undefined }
        const changes = diffConfig(saved, next)
        // A draft that had caught up with the saved config starts again from the current version.
        const version = effectiveChanges(saved, e.changes).length ? e.version : undefined
        return { changes, version: changes.length ? (version ?? configVersion(client)) : undefined }
      })
      // A field the last save was refused on loses its error once it changes.
      const before = draft ?? saved
      clearChangedFieldErrors(before, resolve(before))
    },
    [client, draft, saved],
  )

  const dirty = edits.length > 0 || secrets.dirty
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const draftValue = useMemo(() => ({ draft, setDraft }), [draft, setDraft])
  const store = useMemo(
    () => ({ edits, version: state.version, setEdits, saving, setSaving }),
    [edits, state.version, saving],
  )
  return (
    <DraftStoreContext value={store}>
      <DraftContext value={draftValue}>{children}</DraftContext>
    </DraftStoreContext>
  )
}
