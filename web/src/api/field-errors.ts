import { useMemo, useSyncExternalStore } from 'react'
import type { ErrorSchema } from '@rjsf/utils'
import { locPath, type ValidationError } from './client'
import { deref, getIn, isRecord, type JsonSchema, type Path } from '@/lib/schema'

// Errors from a refused save (422), shown beside the fields they belong to. The save flow calls
// showFieldErrors; every SectionForm reads the ones under its own path; editing a field clears its own.

export type FieldError = { loc: Path; msg: string }

let errors: readonly FieldError[] = []
const listeners = new Set<() => void>()

function publish(next: readonly FieldError[]) {
  errors = next
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function snapshot() {
  return errors
}

/** The field errors in a 422 body (`{detail: [{loc, msg}]}`), with loc from the config root. */
export function fieldErrorsFrom(error: unknown): FieldError[] {
  const detail = (error as ValidationError | undefined)?.detail
  if (!Array.isArray(detail)) return []
  return detail.map((d) => ({ loc: locPath(d.loc), msg: d.msg }))
}

/** The DOM id of the field at a config path. A SectionForm passes fieldId(its path) to rjsf as the id
 * prefix, so ids are unique on the page and a 422's loc finds its field. */
export function fieldId(loc: Path): string {
  return ['cfg', ...loc].join('_')
}

/** Whether the field at `loc` (or a section on the way to it) is advanced in the schema. */
function isAdvancedAt(root: JsonSchema, loc: Path): boolean {
  let current: JsonSchema | undefined = deref(root, root)
  for (const key of loc) {
    const next: unknown = current?.properties?.[String(key)] ?? current?.additionalProperties ?? current?.items
    if (!isRecord(next)) return false
    current = deref(next as JsonSchema, root)
    if (current.level === 'advanced') return true
  }
  return false
}

type ShowOptions = {
  /** The config schema, to tell whether a failing field is advanced. */
  schema: JsonSchema
  /** Turns the page's Advanced toggle on, when a failing field is only shown there. */
  setAdvanced?: (advanced: boolean) => void
}

/** Show a 422's errors on the forms: each beside its field, a section's own (a model validator)
 * beside the section title. Turns Advanced on when a failing field is advanced and scrolls to the
 * first one. Returns the errors it read, so the caller can still toast a summary. */
export function showFieldErrors(error: unknown, { schema, setAdvanced }: ShowOptions): FieldError[] {
  const found = fieldErrorsFrom(error)
  publish(found)
  if (found.length === 0) return found
  if (setAdvanced && found.some((e) => isAdvancedAt(schema, e.loc))) setAdvanced(true)
  // After the forms re-render with the errors (and the advanced fields), scroll to the first one that
  // is on this page: the field itself, else the closest section above it.
  window.setTimeout(() => {
    for (const e of found) {
      for (let n = e.loc.length; n > 0; n--) {
        const id = fieldId(e.loc.slice(0, n))
        const element = document.getElementById(id) ?? document.getElementById(`${id}__title`)
        if (element) {
          element.scrollIntoView({ behavior: 'smooth', block: 'center' })
          if (element instanceof HTMLElement && 'focus' in element) element.focus({ preventScroll: true })
          return
        }
      }
    }
  }, 50)
  return found
}

export function clearFieldErrors() {
  if (errors.length) publish([])
}

/** Drop the errors of fields whose value changed between two drafts (the user is fixing them). */
export function clearChangedFieldErrors(before: unknown, after: unknown) {
  const kept = errors.filter((e) => getIn(before, e.loc) === getIn(after, e.loc))
  if (kept.length !== errors.length) publish(kept)
}

function startsWith(loc: Path, path: Path): boolean {
  return path.every((k, i) => String(loc[i]) === String(k))
}

export type SectionErrors = {
  /** For rjsf's extraErrors: nested by key, `__errors` at each failing field. */
  extraErrors: ErrorSchema | undefined
  /** Messages about the section as a whole (a model validator on it), for beside its title. */
  own: string[]
  /** Messages about nested objects, by the id of the object's title, for beside that title. */
  byTitle: Record<string, string[]>
}

/** The errors under one SectionForm's config path. `objects` tells which relative paths are
 * objects, whose errors go beside their title rather than under a field. */
export function useSectionErrors(path: Path, isObject: (rel: Path) => boolean): SectionErrors {
  const all = useSyncExternalStore(subscribe, snapshot, snapshot)
  return useMemo(() => {
    const own: string[] = []
    const byTitle: Record<string, string[]> = {}
    let extra: Record<string, unknown> | undefined
    for (const e of all) {
      if (!startsWith(e.loc, path)) continue
      const rel = e.loc.slice(path.length)
      if (rel.length === 0) {
        own.push(e.msg)
      } else if (isObject(rel)) {
        const id = fieldId(e.loc)
        byTitle[id] = [...(byTitle[id] ?? []), e.msg]
      } else {
        extra ??= {}
        let node = extra
        for (const key of rel) {
          const k = String(key)
          node[k] = isRecord(node[k]) ? node[k] : {}
          node = node[k] as Record<string, unknown>
        }
        node.__errors = [...((node.__errors as string[] | undefined) ?? []), e.msg]
      }
    }
    return { extraErrors: extra as ErrorSchema | undefined, own, byTitle }
  }, [all, path, isObject])
}
