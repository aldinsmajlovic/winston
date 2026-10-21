import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { $api } from '@/api/client'
import type { ExposedEntity } from '@/api/client'
import type { components } from '@/api/schema'
import type { ActionItem } from '@/components/action-list'
import { humanize, joinNames } from '@/lib/format'

export type HAEntity = components['schemas']['HAEntity']
export type HAExposure = components['schemas']['HAExposure']
export type HARegistry = components['schemas']['HARegistry']
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

/** An entity's name in Home Assistant, from what it said last (the exposure and the registry), else its id. */
export function haNames(client: QueryClient): (id: string) => string {
  const exposure = client.getQueryData<HAExposure>(exposureKey)
  const registry = client.getQueryData<HARegistry>(registryKey)
  const names = new Map<string, string>()
  const known = [...(exposure?.entities ?? []), ...(exposure?.drift ?? []), ...(registry?.groups ?? []).flatMap((g) => g.entities)]
  for (const e of known) if (e.friendly_name && !names.has(e.entity_id)) names.set(e.entity_id, e.friendly_name)
  return (id) => names.get(id) ?? id
}

const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
const total = (m: Record<string, string[]> | undefined) => Object.values(m ?? {}).reduce((n, a) => n + a.length, 0)

/** What an apply did, as the Save confirm said it would: "exposed 3", "stopped exposing 41 to Assist",
 * "added 2 names". */
export function applySummary(a: HAApply): string[] {
  const exposure = [
    a.exposed?.length ? `exposed ${a.exposed.length}` : null,
    a.unexposed?.length ? `stopped exposing ${a.unexposed.length}` : null,
  ].filter((p) => p !== null)
  if (exposure.length) exposure[exposure.length - 1] += ' to Assist'
  const added = total(a.aliases_added)
  const removed = total(a.aliases_removed)
  return [
    ...exposure,
    added ? `added ${count(added, 'name')}` : null,
    removed ? `removed ${count(removed, 'name')}` : null,
  ].filter((p) => p !== null)
}

/** Lines under the toast: what could not be applied, and a setting Winston changed in Home Assistant. */
function applyNotes(a: HAApply): string[] {
  const notes: string[] = []
  if (a.skipped?.length) notes.push(`Not in Home Assistant: ${a.skipped.join(', ')}`)
  if (a.no_registry?.length) notes.push(`Could not add other names to: ${a.no_registry.join(', ')}`)
  if (a.auto_expose_turned_off) notes.push('New devices are no longer exposed to Assist')
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

/** What applying a plan does in Home Assistant, as the Save confirm says it: "exposes 3", "stops exposing
 * 4 to Assist (Front door, Forecast and 2 more)", "adds 2 names", "turns off exposing new devices". With
 * `name` (haNames) it names up to three of what stops being exposed. */
export function planParts(plan: ExposurePlan, name?: (id: string) => string): string[] {
  const off = plan.unexpose ?? []
  const named = name && off.length ? ` (${joinNames(off.length > 3 ? [...off.slice(0, 2).map(name), `${off.length - 2} more`] : off.map(name))})` : ''
  const exposure = [
    plan.expose?.length ? `exposes ${plan.expose.length}` : null,
    off.length ? `stops exposing ${off.length}` : null,
  ].filter((p) => p !== null)
  if (exposure.length) exposure[exposure.length - 1] += ` to Assist${named}`
  const added = total(plan.aliases_add)
  const removed = total(plan.aliases_remove)
  return [
    ...exposure,
    added ? `adds ${count(added, 'name')}` : null,
    removed ? `removes ${count(removed, 'name')}` : null,
    plan.auto_expose_off ? 'turns off exposing new devices' : null,
  ].filter((p) => p !== null)
}

/** Home Assistant's tools as action rows: they run on their own, and only look when every one of them
 * does. Only a script's description, the household's own words, shows on the row; Home Assistant's text
 * for the model is under Details. A script with no description says why he may not pick it. */
export function haActionItems(tools: HATool[]): ActionItem[] {
  return tools.map((t) => ({
    name: t.names.join(' '),
    title: t.label,
    summary: t.domain === 'script' ? t.description || 'No description in Home Assistant, so he may not know when to use it.' : '',
    brainName: t.names.join(', '),
    description: t.description,
    declared: t.tier,
    tier: t.tier,
    enabled: true,
    thirdParty: false,
  }))
}

// The rows of the page's list: an entity of the draft list with Home Assistant's side of it.

export type EntityRow = {
  id: string
  entry: ExposedEntity
  /** Home Assistant's side: name, aliases, room, device and state; absent when it is not known. */
  ha?: HAEntity
  /** What is not right yet, in words ("Waiting for Save"); null when it is as the list has it, or unknown. */
  badge: string | null
}

const fold = (s: string) => s.trim().toLocaleLowerCase()

/** The name he hears: the list's, else Home Assistant's, else the id. */
export const heardName = (r: EntityRow) => r.entry.name || r.ha?.friendly_name || r.id

/** The other names he answers to: the list's own, then the ones only Home Assistant has, without the
 * name itself or repeats. */
export function otherNames(r: EntityRow): { own: string[]; ha: string[] } {
  const seen = new Set([fold(heardName(r))])
  const keep = (names: string[]) =>
    names.filter((a) => {
      if (typeof a !== 'string' || !a.trim() || seen.has(fold(a))) return false
      seen.add(fold(a))
      return true
    })
  return { own: keep(r.entry.aliases ?? []), ha: keep(r.ha?.aliases ?? []) }
}

/** Home Assistant's own name for it, muted under the name he hears, when that differs. */
export function haName(r: EntityRow): string | null {
  const shown = fold(heardName(r))
  const friendly = r.ha?.friendly_name
  if (friendly && fold(friendly) !== shown) return `In Home Assistant: ${friendly}`
  const device = r.ha?.device
  if (device && fold(device) !== shown) return `Device: ${device}`
  return null
}

/** Whether a row matches a search: names, room and id. */
export function matches(r: EntityRow, query: string): boolean {
  const q = fold(query)
  if (!q) return true
  const { own, ha } = otherNames(r)
  const hay = [heardName(r), r.id, r.ha?.friendly_name, r.ha?.device, r.ha?.area, ...own, ...ha]
  return hay.some((h) => h != null && fold(h).includes(q))
}

export type Group = { name: string; rows: EntityRow[] }

/** The listed entities by Home Assistant's rooms, each sorted by name, the ones without a room last. */
export function groupByArea(rows: EntityRow[], areasKnown: boolean): Group[] {
  const noRoom = areasKnown ? 'No room' : 'Devices'
  const groups = new Map<string, EntityRow[]>()
  for (const r of rows) {
    const name = r.ha?.area ?? noRoom
    groups.set(name, [...(groups.get(name) ?? []), r])
  }
  return [...groups]
    .map(([name, rs]) => ({ name, rows: rs.sort((a, b) => heardName(a).localeCompare(heardName(b))) }))
    .sort((a, b) => (a.name === noRoom ? 1 : b.name === noRoom ? -1 : a.name.localeCompare(b.name)))
}

// Home Assistant's kinds as a person names them, for the ones usually shared whole; the rest read as
// their id does ("Calendar").
const KINDS: Record<string, string> = { person: 'People', todo: 'To-do lists', zone: 'Zones' }

/** "People", "To-do lists", "Weather". */
export const kindName = (domain: string) => KINDS[domain] ?? humanize(domain)
