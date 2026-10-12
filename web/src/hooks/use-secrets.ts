import { useCallback, useMemo, useState, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { $api, errorText, fetchClient, type SecretStatus } from '@/api/client'
import { statusKey } from '@/api/status'

// Keys pasted into the settings pages wait here, beside the config draft and never inside it, until
// Save writes them to data/.env with PUT /api/secrets/{name}. The API never sends a value back, so
// once saved a key is only "Set" or "Not set". The draft lives outside React so it survives
// navigation between pages like the config draft does.

type Pending = Readonly<Record<string, string>>

let pending: Pending = {}
const listeners = new Set<() => void>()

function publish(next: Pending) {
  pending = next
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function snapshot(): Pending {
  return pending
}

/** Put a pasted key in the draft under its variable name; an empty value takes it out again. */
export function setSecret(name: string, value: string) {
  const next = { ...pending }
  if (value === '') delete next[name]
  else next[name] = value
  publish(next)
}

/** Drop pasted keys from the draft: the named ones, or all (Discard). */
export function discardSecrets(names?: string[]) {
  if (!names) return publish({})
  const next = { ...pending }
  for (const name of names) delete next[name]
  publish(next)
}

export const secretsKey = $api.queryOptions('get', '/api/secrets').queryKey

/** Which variables are set (never their values), what names them and what reads them. */
export function useSecretStatus() {
  const query = $api.useQuery('get', '/api/secrets', undefined, { staleTime: 10_000 })
  const byName = useMemo(() => new Map((query.data ?? []).map((s) => [s.name, s])), [query.data])
  return { ...query, byName }
}

/** What a save of the pending keys restarts, as far as winstond knows today: a name no saved config
 * uses yet reads nothing until the config that names it is saved. */
export function secretRestarts(names: Iterable<string>, byName: Map<string, SecretStatus>): string[] {
  const out = new Set<string>()
  for (const name of names) for (const r of byName.get(name)?.restarts ?? []) out.add(r)
  return [...out]
}

export type SecretsSaved = {
  /** Names written to data/.env. */
  saved: string[]
  /** What reads them, as the server reported after the write (may include "control"). */
  restarts: string[]
  /** One line per key that was refused ("TTS_API_KEY: reason"); those stay in the draft. */
  errors: string[]
}

/** The secrets draft for the save flow and the fields.
 *
 * - `pending`: name -> pasted value; `dirty` when anything waits.
 * - `set(name, value)`, `discard(names?)`: edit the draft (Discard calls `discard()`).
 * - `save()`: after the config is saved (so a new variable name is already in it), PUT each pending
 *   key; saved ones leave the draft, refused ones stay and come back in `errors` for a toast.
 * - `remove(name)`: DELETE a stored key now (the field confirms first).
 */
export function useSecretsDraft() {
  const client = useQueryClient()
  const current = useSyncExternalStore(subscribe, snapshot, snapshot)
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(() => {
    void client.invalidateQueries({ queryKey: secretsKey })
    void client.invalidateQueries({ queryKey: statusKey })
    // Lists that need a key (Discord users with the bot token) can answer now.
    void client.invalidateQueries({ queryKey: ['get', '/api/options/{name}'] })
  }, [client])

  const save = useCallback(async (): Promise<SecretsSaved> => {
    const entries = Object.entries(snapshot())
    const result: SecretsSaved = { saved: [], restarts: [], errors: [] }
    if (entries.length === 0) return result
    setSaving(true)
    const restarts = new Set<string>()
    try {
      for (const [name, value] of entries) {
        const { data, error } = await fetchClient.PUT('/api/secrets/{name}', {
          params: { path: { name } },
          body: { value },
        })
        if (data) {
          result.saved.push(name)
          for (const r of data.restarts) restarts.add(r)
        } else {
          result.errors.push(`${name}: ${errorText(error)}`)
        }
      }
    } catch (error) {
      result.errors.push(errorText(error))
    } finally {
      setSaving(false)
    }
    discardSecrets(result.saved)
    result.restarts = [...restarts]
    refresh()
    return result
  }, [refresh])

  const remove = useCallback(
    async (name: string): Promise<string | null> => {
      try {
        const { error } = await fetchClient.DELETE('/api/secrets/{name}', { params: { path: { name } } })
        if (error) return errorText(error)
      } catch (error) {
        return errorText(error)
      }
      discardSecrets([name])
      refresh()
      return null
    },
    [refresh],
  )

  return {
    pending: current,
    dirty: Object.keys(current).length > 0,
    saving,
    set: setSecret,
    discard: discardSecrets,
    save,
    remove,
  }
}
