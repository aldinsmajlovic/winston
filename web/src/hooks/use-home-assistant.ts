import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { $api } from '@/api/client'
import type { ExposedEntity } from '@/api/client'
import type { components } from '@/api/schema'

export type HAEntity = components['schemas']['HAEntity']
export type HAExposure = components['schemas']['HAExposure']
export type HAApply = components['schemas']['HAApply']
export type HATool = components['schemas']['HATool']
export type ExposurePlan = components['schemas']['ExposurePlan']

const exposureKey = $api.queryOptions('get', '/api/providers/home_assistant/exposure').queryKey
const registryKey = $api.queryOptions('get', '/api/providers/home_assistant/registry').queryKey
const toolsKey = $api.queryOptions('get', '/api/providers/home_assistant/tools').queryKey

/** Home Assistant as it has the saved list, its whole registry by room, and the tools it offers. All
 * three follow the saved config, so they are only asked for while the saved config has it on. */
export function useHomeAssistant(on: boolean) {
  const exposure = $api.useQuery('get', '/api/providers/home_assistant/exposure', undefined, { enabled: on, retry: false })
  const registry = $api.useQuery('get', '/api/providers/home_assistant/registry', undefined, {
    enabled: on,
    retry: false,
    staleTime: 30_000,
  })
  const tools = $api.useQuery('get', '/api/providers/home_assistant/tools', undefined, { enabled: on, retry: false })
  return { exposure, registry, tools }
}

/** Read Home Assistant again: what it exposes, its registry and its tools. */
export function refreshHomeAssistant(client: QueryClient) {
  for (const queryKey of [exposureKey, registryKey, toolsKey]) void client.invalidateQueries({ queryKey })
}

const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
const total = (m: Record<string, string[]> | undefined) => Object.values(m ?? {}).reduce((n, a) => n + a.length, 0)

/** What an apply did, in a few words: "3 exposed, 1 alias removed". */
export function applySummary(a: HAApply): string[] {
  const parts: string[] = []
  if (a.exposed?.length) parts.push(`${a.exposed.length} exposed`)
  if (a.unexposed?.length) parts.push(`${a.unexposed.length} un-exposed`)
  const added = total(a.aliases_added)
  const removed = total(a.aliases_removed)
  if (added) parts.push(`${count(added, 'alias', 'aliases')} added`)
  if (removed) parts.push(`${count(removed, 'alias', 'aliases')} removed`)
  return parts
}

/** Lines under the toast: what could not be applied, and a setting Winston changed in Home Assistant. */
function applyNotes(a: HAApply): string[] {
  const notes: string[] = []
  if (a.skipped?.length) notes.push(`Not in Home Assistant: ${a.skipped.join(', ')}`)
  if (a.no_registry?.length) notes.push(`Cannot take aliases: ${a.no_registry.join(', ')}`)
  if (a.auto_expose_turned_off) notes.push('New entities are no longer exposed automatically')
  return notes
}

/** The toast after a save: the summary of what Home Assistant got, or why it got nothing. */
export function toastSaved(apply: HAApply | null | undefined) {
  if (!apply) return void toast.success('Saved')
  const parts = applySummary(apply)
  if (!apply.applied || apply.error) {
    const done = parts.length ? `Done before it stopped: ${parts.join(', ')}` : undefined
    toast.warning(`Saved; Home Assistant was not updated: ${apply.error ?? 'unknown reason'}`, { description: done })
    return
  }
  const notes = applyNotes(apply)
  toast.success(parts.length ? `Saved; ${parts.join(', ')}` : 'Saved', {
    description: notes.length ? notes.join('. ') : undefined,
  })
}

/** Apply the saved list again, for when Home Assistant was away during a save. */
export function useApplyNow() {
  const client = useQueryClient()
  return $api.useMutation('post', '/api/providers/home_assistant/apply', {
    onSuccess: (apply) => {
      refreshHomeAssistant(client)
      if (!apply.applied || apply.error) {
        toast.warning(`Home Assistant was not updated: ${apply.error ?? 'unknown reason'}`)
        return
      }
      const parts = applySummary(apply)
      const notes = applyNotes(apply)
      toast.success(parts.length ? `Applied; ${parts.join(', ')}` : 'Already applied', {
        description: notes.length ? notes.join('. ') : undefined,
      })
    },
  })
}

// The rows of the page's list: an entity of the draft list with Home Assistant's side of it.

/** Whether a listed entity is in Home Assistant as the list has it. */
export type RowStatus =
  | { kind: 'applied' }
  | { kind: 'pending'; why: string[] }
  | { kind: 'missing' }
  | { kind: 'unknown'; why: string }

export type EntityRow = {
  id: string
  entry: ExposedEntity
  /** Home Assistant's side: name, aliases, room, device and state; absent when it is not known. */
  ha?: HAEntity
  status: RowStatus
}

const fold = (s: string) => s.trim().toLocaleLowerCase()

/** The name he hears: the list's, else Home Assistant's, else the id. */
export const heardName = (r: EntityRow) => r.entry.name || r.ha?.friendly_name || r.id

/** Every alias he can be called by: the list's, then the ones Home Assistant has besides, without the
 * name itself. */
export function aliasesOf(r: EntityRow): string[] {
  const seen = new Set([fold(heardName(r))])
  const out: string[] = []
  for (const a of [...(r.entry.aliases ?? []), ...(r.ha?.aliases ?? [])]) {
    if (!a.trim() || seen.has(fold(a))) continue
    seen.add(fold(a))
    out.push(a)
  }
  return out
}

/** Home Assistant's own name for it, muted under the name he hears, when that differs. */
export function haName(r: EntityRow): string | null {
  const shown = fold(heardName(r))
  const friendly = r.ha?.friendly_name
  if (friendly && fold(friendly) !== shown) return `${friendly} in Home Assistant`
  const device = r.ha?.device
  if (device && fold(device) !== shown) return device
  return null
}

/** Whether a row matches a search: names, aliases, room and id. */
export function matches(r: EntityRow, query: string): boolean {
  const q = fold(query)
  if (!q) return true
  const hay = [heardName(r), r.id, r.ha?.friendly_name, r.ha?.device, r.ha?.area, ...aliasesOf(r)]
  return hay.some((h) => h != null && fold(h).includes(q))
}

export type Group = { name: string; rows: EntityRow[] }

/** The listed entities by Home Assistant's rooms, sorted by name, the ones without a room last. */
export function groupByArea(rows: EntityRow[], areasKnown: boolean): Group[] {
  const noRoom = areasKnown ? 'No room' : 'Entities'
  const groups = new Map<string, EntityRow[]>()
  for (const r of rows) {
    const name = r.ha?.area ?? noRoom
    groups.set(name, [...(groups.get(name) ?? []), r])
  }
  return [...groups]
    .map(([name, rs]) => ({ name, rows: rs }))
    .sort((a, b) => (a.name === noRoom ? 1 : b.name === noRoom ? -1 : a.name.localeCompare(b.name)))
}

// Types the picker hides until asked for: there are many of them in a typical home (26 Plex players,
// 17 updates) and he rarely needs them. "All types" or picking the type shows them; nothing is removed.
export const NOISY_DOMAINS = [
  'update',
  'media_player',
  'device_tracker',
  'event',
  'image',
  'number',
  'select',
  'text',
  'tts',
  'stt',
  'wake_word',
  'conversation',
  'zone',
  'sun',
]
